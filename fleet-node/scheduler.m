// CLIProxyAPI C ABI v1. Only scheduling identifiers cross the loopback seam.
// No prompts, response bodies, auth metadata or provider tokens are retained.
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>

typedef struct { void *ptr; size_t len; } cliproxy_buffer;
typedef struct { uint32_t abi_version; void *host_ctx; void *call; void *free_buffer; } cliproxy_host_api;
typedef struct {
  uint32_t abi_version;
  int (*call)(const char*, const uint8_t*, size_t, cliproxy_buffer*);
  void (*free_buffer)(void*, size_t);
  void (*shutdown)(void);
} cliproxy_plugin_api;

static NSDictionary *denied(NSString *message) {
  return @{ @"ok": @NO, @"error": @{ @"code": @"fleet_admission_wait", @"http_status": @503, @"message": message } };
}

static NSString *digest(NSString *input) {
  NSData *data = [input dataUsingEncoding:NSUTF8StringEncoding];
  unsigned char hash[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256(data.bytes, (CC_LONG)data.length, hash);
  NSMutableString *result = [NSMutableString string];
  for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) [result appendFormat:@"%02x", hash[i]];
  return result;
}

static NSString *header(NSDictionary *headers, NSArray *names) {
  for (NSString *name in names) for (NSString *key in headers) {
    if ([key caseInsensitiveCompare:name] != NSOrderedSame) continue;
    id value = headers[key];
    if ([value isKindOfClass:[NSArray class]]) value = [value firstObject];
    if ([value isKindOfClass:[NSString class]] && [value length] <= 512) return value;
  }
  return @"";
}

static NSString *privateKey(void) {
  NSString *path = [NSHomeDirectory() stringByAppendingPathComponent:@".local/share/codex-fleet/runtime/admin.key"];
  NSArray *parts = path.pathComponents;
  int directory = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (directory < 0) return nil;
  for (NSUInteger i = 1; i + 1 < parts.count; i++) {
    int child = openat(directory, [parts[i] fileSystemRepresentation], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    close(directory);
    if (child < 0) return nil;
    directory = child;
  }
  int file = openat(directory, [[parts lastObject] fileSystemRepresentation], O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  close(directory);
  if (file < 0) return nil;
  struct stat info;
  char buffer[4096];
  ssize_t length = -1;
  if (!fstat(file, &info) && S_ISREG(info.st_mode) && info.st_uid == getuid() && (info.st_mode & 0777) == 0600)
    length = read(file, buffer, sizeof(buffer));
  close(file);
  if (length <= 0 || length >= sizeof(buffer)) return nil;
  NSString *key = [[[NSString alloc] initWithBytes:buffer length:length encoding:NSUTF8StringEncoding]
    stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
  memset(buffer, 0, sizeof(buffer));
  return key.length ? key : nil;
}

static NSDictionary *coordinator(NSString *operation, NSDictionary *body) {
  NSString *key = privateKey();
  if (!key) return nil;
  NSMutableURLRequest *http = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[@"http://127.0.0.1:8765/api/gateway/" stringByAppendingString:operation]]
    cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:4];
  http.HTTPMethod = @"POST";
  [http setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
  [http setValue:[@"Bearer " stringByAppendingString:key] forHTTPHeaderField:@"Authorization"];
  http.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil];
  __block NSData *responseData = nil;
  __block NSInteger status = 0;
  dispatch_semaphore_t done = dispatch_semaphore_create(0);
  NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
  configuration.connectionProxyDictionary = @{};
  NSURLSession *client = [NSURLSession sessionWithConfiguration:configuration];
  NSURLSessionDataTask *task = [client dataTaskWithRequest:http completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
    if (!error && data.length < 65536) { responseData = data; status = [(NSHTTPURLResponse*)response statusCode]; }
    dispatch_semaphore_signal(done);
  }];
  [task resume];
  long timedOut = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
  [client invalidateAndCancel];
  if (timedOut || status != 200 || !responseData) return nil;
  id result = [NSJSONSerialization JSONObjectWithData:responseData options:0 error:nil];
  return [result isKindOfClass:[NSDictionary class]] ? result : nil;
}

static NSDictionary *pick(NSDictionary *request) {
  NSArray *raw = request[@"Candidates"] ?: request[@"candidates"];
  if (![raw isKindOfClass:[NSArray class]] || raw.count > 100) return denied(@"Invalid routing candidates");
  NSMutableArray *candidates = [NSMutableArray array];
  for (NSDictionary *item in raw) {
    if (![item isKindOfClass:[NSDictionary class]]) continue;
    NSString *provider = item[@"Provider"] ?: item[@"provider"];
    NSString *identifier = item[@"ID"] ?: item[@"id"];
    if ([provider isEqual:@"codex"] && [identifier isKindOfClass:[NSString class]])
      [candidates addObject:@{@"id": identifier, @"provider": @"codex"}];
  }
  if (!candidates.count) return denied(@"Only the two configured Codex accounts are routed by this plugin");
  NSDictionary *options = request[@"Options"] ?: request[@"options"] ?: @{};
  NSDictionary *headers = options[@"Headers"] ?: options[@"headers"] ?: @{};
  NSDictionary *metadata = options[@"Metadata"] ?: options[@"metadata"] ?: @{};
  NSString *session = header(headers, @[@"Session-Id", @"Session_id", @"X-Session-ID", @"X-Session-Affinity"]);
  NSString *parent = [metadata[@"parent_session_id"] isKindOfClass:[NSString class]] ? metadata[@"parent_session_id"] : @"";
  NSString *caller = [metadata[@"caller_scope"] isKindOfClass:[NSString class]] ? metadata[@"caller_scope"] : @"owner";
  NSDictionary *body = @{@"provider": @"codex", @"model": request[@"Model"] ?: request[@"model"] ?: @"",
    @"session": session, @"parent": parent, @"scope": digest(caller), @"candidates": candidates};

  NSDictionary *result = coordinator(@"pick", body);
  if (!result) return denied(@"Coordinator is unavailable; no fallback account was selected");
  if (![result isKindOfClass:[NSDictionary class]] || ![result[@"handled"] boolValue]) return denied(@"Routing response was invalid");
  if ([result[@"reject"] boolValue]) return denied(result[@"reject_reason"] ?: @"No eligible account");
  if (![result[@"auth_id"] isKindOfClass:[NSString class]]) return denied(@"Routing response did not select an account");
  return @{@"ok": @YES, @"result": result};
}

static int call(const char *method, const uint8_t *bytes, size_t length, cliproxy_buffer *out) {
  @autoreleasepool {
    if (!out) return 1;
    out->ptr = NULL; out->len = 0;
    NSDictionary *response = nil;
    @try {
      NSString *name = method ? [NSString stringWithUTF8String:method] : @"";
      if ([name isEqual:@"plugin.register"] || [name isEqual:@"plugin.reconfigure"]) {
        response = @{@"ok": @YES, @"result": @{@"schema_version": @6,
          @"metadata": @{@"name": @"ranton-router", @"version": @"0.1.0", @"author": @"ranton fleet",
            @"GitHubRepository": @"https://github.com/royceanton/ranton-fleet-console"},
          @"capabilities": @{@"scheduler": @YES, @"scheduler_across_priorities": @YES, @"request_interceptor": @YES, @"request_lifecycle_plugin": @YES}}};
      } else if ([name isEqual:@"scheduler.pick"] && length < 2 * 1024 * 1024) {
        id request = [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:bytes length:length] options:0 error:nil];
        response = [request isKindOfClass:[NSDictionary class]] ? pick(request) : denied(@"Invalid scheduler request");
      } else if ([name isEqual:@"request.intercept_before"] || [name isEqual:@"request.intercept_after"] || [name isEqual:@"request.complete"]) {
        id request = length < 2 * 1024 * 1024 ? [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:bytes length:length] options:0 error:nil] : nil;
        if ([request isKindOfClass:[NSDictionary class]] && ![name isEqual:@"request.intercept_after"]) {
          NSString *identifier = request[@"RequestID"];
          NSString *model = request[@"Model"];
          NSString *state = [name isEqual:@"request.complete"] ? request[@"Outcome"] : @"running";
          if ([identifier isKindOfClass:[NSString class]] && [model isKindOfClass:[NSString class]] && model.length && [state isKindOfClass:[NSString class]]) {
            coordinator(@"event", @{@"id": identifier, @"model": model, @"state": state, @"statusCode": request[@"StatusCode"] ?: @0});
          }
        }
        // Never modify headers, payloads, streaming protocol or response content.
        response = @{@"ok": @YES, @"result": @{}};
      } else if ([name isEqual:@"plugin.quiesce"] || [name isEqual:@"plugin.shutdown"]) {
        response = @{@"ok": @YES, @"result": @{}};
      } else response = denied(@"Unsupported plugin operation");
    } @catch (NSException *exception) { response = denied(@"Router stopped safely after an invalid request"); }
    NSData *encoded = [NSJSONSerialization dataWithJSONObject:response options:0 error:nil];
    out->len = encoded.length; out->ptr = malloc(encoded.length);
    if (!out->ptr) { out->len = 0; return 1; }
    memcpy(out->ptr, encoded.bytes, encoded.length);
    return 0;
  }
}

static void free_buffer(void *ptr, size_t length) { (void)length; free(ptr); }
static void shutdown_plugin(void) {}

__attribute__((visibility("default"))) int cliproxy_plugin_init(const cliproxy_host_api *host, cliproxy_plugin_api *plugin) {
  if (!host || host->abi_version != 1 || !plugin) return 1;
  plugin->abi_version = 1; plugin->call = call; plugin->free_buffer = free_buffer; plugin->shutdown = shutdown_plugin;
  return 0;
}
