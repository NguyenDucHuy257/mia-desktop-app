# Audit license, device binding và silent migration

Ngày audit: 2026-08-30

Repository: `NguyenDucHuy257/mia-desktop-app`

Branch/HEAD: `develop` / `bb16382a4de04fc38a55f89a07a2b35b6e1c565c`
Mốc remote: `origin/develop` cùng SHA tại thời điểm audit

## 1. Phạm vi và kết luận

Đây là output của Stage 1. Audit này **không thay đổi production license flow**.

Desktop đã có nền móng tốt để làm device proof: một Ed25519 identity ổn định, private key được Electron `safeStorage` bảo vệ và IPC sender được kiểm tra. Tuy nhiên, ứng dụng chưa có một hệ thống license hoàn chỉnh: chưa có `LicenseManager`, state machine, V2 server client, token verification, offline lease, legacy detector, hardware profile, activation UI hoặc license gate.

Silent migration chưa thể triển khai an toàn chỉ từ repository này. Workspace không có source key-server `/opt/keys_app`, `MIA/vip.txt`, source MIA V1.4, hoặc fixtures đã khử dữ liệu nhạy cảm cho các generation V2/V3/MAK. V1 có công thức đã được tài liệu giao việc xác nhận; các generation khác phải giữ trạng thái chưa chứng minh cho đến khi có source hoặc fixture có provenance.

Quyết định rollout:

1. Giữ production behavior hiện tại trong Stage 1.
2. Không hiện Phone Form trước legacy detection khi V2 được bật.
3. Đưa client V2 sau feature flag, mặc định tắt cho đến khi server và migration tests đạt gate.
4. Không sunset `/verify-key` hoặc sửa `MIA/vip.txt` trong pilot.
5. Không tuyên bố tỷ lệ silent migration trước khi chạy analyzer trên full legacy store.

## 2. Source và nhánh đã kiểm tra

- `develop` là nhánh có commit liên quan mới nhất trong repository tại thời điểm audit.
- `origin/develop` không ahead/behind so với working tree.
- Các nhánh `refactor/source-runtime-parity`, `feat/offline-phase-065-full-ui`, `feat/offline-phase-07-release` đều cũ hơn và không chứa license implementation mới hơn.
- `origin/main` đã diverge và cũ hơn phần implementation hiện hành; không dùng làm source để thiết kế license.
- Git history và tất cả remote branch không chứa `vip.txt`, `/verify-key`, `Win32_DiskDrive` legacy generator hoặc key-server implementation.

Các file desktop đã audit:

- `electron/main.cjs`
- `electron/preload.cjs`
- `electron/device-identity.cjs`
- `electron/security-policy.cjs`
- `src/main.tsx`
- `src/App.tsx`
- `src/components/AppShell.tsx`
- `src/lib/runtime-bridge.ts`
- `src/styles/tokens.css`
- `src/styles/global.css`
- `package.json`
- `docs/REPO-AUDIT.md`
- `docs/offline-phases/PHASE-00-ARCHITECTURE.md`
- `docs/offline-phases/PHASE-06-ACTIVATION.md`
- `docs/offline-phases/PHASE-07-REPORT.md`
- `tests/unit/device-identity.test.ts`
- package/renderer secret scan scripts

Source snapshot bổ sung ngày 2026-08-30:

- `D:\Downloads\app.py`, SHA-256 `6d0ea73b9a65a8d922869b5abb0114d0b27471146eb5e1d80925617e48500be6`;
- `D:\Downloads\auth.py`, SHA-256 `6bd145ca4cc2f9b47e9c4e65e1f432b814df4a96038c78e062150fc644bed8ac`;
- `D:\Downloads\vip.txt`, được cung cấp như snapshot kho MIA, SHA-256 `a23680826c65f7738c224ed4bc1a907e671defea24100d7d28535c0f32173839`.

Các file này không nằm trong một server repository có history/dependency/deployment tests, nên audit có thể xác nhận code snapshot nhưng chưa xác nhận đây là revision đang chạy production.

## 3. Kiến trúc hiện tại

### 3.1 Electron security boundary

`electron/main.cjs` dùng `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity: true`. Mọi IPC hiện có gọi `assertTrustedSender()` và production navigation bị giới hạn vào entry `file:` tin cậy. Đây là boundary cần giữ.

`securityDirectory()` trả về:

```text
<Electron userData>/security
```

Thư mục này nằm ngoài installer, `app.asar`, runtime Python và `Program Files`, nên phù hợp cho identity update-safe.

### 3.2 Device identity hiện có

`electron/device-identity.cjs`:

- sinh Ed25519 key pair bằng Node crypto;
- lưu private PKCS#8 PEM vào `device-private-key.bin` sau `safeStorage.encryptString()`;
- lưu public SPKI PEM vào `device-public-key.pem`;
- fingerprint là SHA-256 hex của public PEM;
- ký server challenge bằng private key và trả signature base64;
- không trả private key qua IPC.

Unit tests hiện xác nhận identity ổn định giữa hai lần đọc và signature verify được với public key.

Điểm cần harden trước rollout:

- file key/token hiện được ghi trực tiếp, chưa temp + atomic replace;
- chưa kiểm tra public/private pair còn khớp khi file hỏng hoặc bị thay riêng lẻ;
- chưa có file permission/ownership recovery test thực tế trên Windows;
- chưa có packaged reinstall/update test chứng minh giữ nguyên identity;
- chưa có copy-security-directory-to-another-PC test với `safeStorage` thật;
- fingerprint hiện hash textual PEM; contract server phải pin đúng canonicalization này hoặc chuyển có version sang DER fingerprint.

### 3.3 License primitive hiện có

`electron/main.cjs` có ba IPC primitive:

- `mia:device-identity`
- `mia:sign-device-challenge`
- `mia:license-store`

`mia:license-store` kiểm tra token là string dài 16–8192 rồi mã hóa bằng `safeStorage` vào `license-token.bin`. Không có IPC đọc token, nhưng cũng chưa có main-process verifier sử dụng token đó.

Rủi ro thiết kế:

- trusted renderer có thể yêu cầu ký challenge tùy ý;
- trusted renderer có thể overwrite token bằng chuỗi bất kỳ đạt length check;
- chưa có token schema, issuer, audience, expiry hoặc signature validation;
- chưa có atomic write, rollback hoặc corrupt-token quarantine;
- chưa có revoke, offline lease hay retry classification.

V2 phải chuyển sang IPC high-level do `LicenseManager` sở hữu. Các primitive cũ cần được deprecate sau compatibility window; renderer không được đọc raw token/private key hoặc tự ghép server request.

### 3.4 Renderer và UI hiện tại

`src/main.tsx` render `<App />` trực tiếp trong React `StrictMode`. `App.tsx` lập tức khởi tạo account/job/result/artifact lifecycle và render `AppShell`; chưa có bước resolve license trước main workspace.

`AppShell` chứa sidebar/topbar và design language đang dùng Inter, nền trắng, blue/green accents, shared CSS tokens. Navigation `settings` hiện render `UtilityPage`; chưa có settings card bản quyền.

License UI cần đứng trước việc render/khởi tạo các lifecycle nhạy cảm. Không chỉ phủ một modal lên `App`, vì khi đó account/runtime operations vẫn có thể chạy phía sau.

### 3.5 Runtime local và dữ liệu hóa đơn

Crawler vẫn là Python runtime local qua JSON-RPC stdin/stdout với một logical sequential worker. License không được đưa vào Python crawler, không được thêm HTTP control server, và không được xóa/migrate account, session, job, SQLite invoice, artifact hoặc export preferences.

License verification thuộc Electron main process. Chỉ sau state `active`/offline lease hợp lệ renderer mới mount application workspace. Việc này không thay đổi crawler worker invariant.

## 4. Legacy schemas

| Schema | Mẫu | Mức chứng minh | Source path | Hành động |
|---|---|---|---|---|
| MIA V1 | `key<29 hex>` | Công thức được input handoff xác nhận | Source V1.4 không có trong workspace; path chưa xác minh | Có thể implement sau khi thêm golden fixture |
| MIA V2 phone | `KEY<29 hex><phone>` | Format được handoff báo đã quan sát | Generator/source chưa có | Chưa implement formula; chỉ parse khi có fixture/source |
| Full hash | `key<64 hex>` | Format được handoff báo đã quan sát | Chưa có | Adapter dừng ở audit |
| MAK | `MAK-XXXX-...` | Format được handoff báo đã quan sát | Chưa có | Manual/adapter riêng sau khi có source |
| Custom/malformed | không cố định | Historical store được mô tả không sạch | Chưa có | Bảo toàn raw record, không auto claim |

### 4.1 V1 formula đã xác nhận

V1 phải tái tạo byte-for-byte:

```text
disk = physical Win32_DiskDrive đầu tiên có int(Size) > 0
serial = SerialNumber.strip(), hoặc "N/A" nếu rỗng
hash29 = SHA256(serial + decimal_string(int(Size))).hex()[0:29]
legacy_key = "key" + hash29
```

Không thêm separator, không đổi casing prefix và không dùng logical volume serial.

Candidate ordering dự kiến:

1. candidate từ chính thứ tự `Win32_DiskDrive` legacy;
2. các physical disk còn lại theo enumeration order, chỉ để tăng recovery coverage;
3. server chỉ exact-match record tồn tại; client candidate không bao giờ tự tạo license.

### 4.2 V2/V3/MAK chưa chứng minh

Không được suy ra V2 chỉ vì suffix trông giống số điện thoại. Chỉ normalize một field thành phone khi nó đạt policy rõ ràng (dự kiến VN `^0[0-9]{9}$`) và provenance cho biết field đó là phone.

Không implement full-hash hoặc MAK adapter từ pattern. Những schema này phải trả `unsupported/manual_verification`, không được approximate-match.

### 4.3 Tỷ lệ schema và silent migration

Analyzer read-only `scripts/analyze-mia-legacy-licenses.mjs` đã chạy trên snapshot có SHA nêu trên, với ngày đánh giá expiry `2026-08-30`. Analyzer không output raw key, phone, legacy row hoặc hardware ID.

| Phân loại | Records | Tỷ lệ |
|---|---:|---:|
| V1 `key<29hex>` | 589 | 33,07% |
| V2 observed `KEY<29hex><phone>` | 1.164 | 65,36% |
| Full hash `key<64hex>` | 7 | 0,39% |
| MAK | 3 | 0,17% |
| Custom/unsupported | 18 | 1,01% |
| Tổng non-empty | 1.781 | 100% |

Hai schema V1/V2 observed chiếm 1.753/1.781, tương đương 98,43%. Snapshot có 1.777 unique key, bốn duplicate-key groups/rows nhưng không có raw line trùng hoàn toàn. Expiry gồm 1.745 current, 25 expired và 11 malformed.

Trong 1.745 record còn hạn:

- 1.720 thuộc hai schema V1/V2 observed, tương đương 98,57% schema-addressable;
- có 1.681 distinct `hash29`;
- 1.661 hash chỉ ánh xạ một current record;
- 20 hash ambiguous chứa tổng cộng 59 current records;
- conservative unique-hash coverage là 1.661/1.745, tương đương 95,19%.

98,57% **không phải** tỷ lệ migration thực tế. Đây là trần schema-addressable có điều kiện; actual rate còn phụ thuộc legacy disk vẫn reconstruct được, local phone/evidence, duplicate resolution và server proof. 95,19% là estimate bảo thủ cho unique current hash mapping, cũng chỉ áp dụng khi client reconstruct đúng hash máy.

Metadata có field trông đúng VN phone regex ở 574/589 V1 rows và 1.142/1.164 V2 rows. Đây chỉ là shape evidence, không tự chứng minh field provenance. Không attach phone cho đến khi parser xác nhận đúng column semantics.

Chạy lại phép đo:

```powershell
node scripts/analyze-mia-legacy-licenses.mjs D:\Downloads\vip.txt --as-of 2026-08-30
```

Trước rollout phải chạy lại trên canonical server file và đối chiếu SHA/count; snapshot local có thể stale.

## 5. Startup state machine bắt buộc

```text
app ready
  -> LicenseManager.initialize()
     -> protected V2 token exists?
        -> yes: challenge + Ed25519 verify
           -> valid: active
           -> network failure: evaluate signed offline lease
           -> revoked/expired: terminal UI state
        -> no/invalid recoverable state: legacy detection
           -> exact candidate match: transactional silent migration
              -> phone known: active
              -> phone absent: legacy_phone_pending policy (không phải new user)
           -> ambiguous: verification_required/manual recovery
           -> no_match: phone_required
```

Phone Form không được render trong lúc state là `checking` hoặc `migrating`. Network failure cũng không được biến thành `no_match`/new customer.

State model đề xuất:

```text
checking | migrating | active | legacy_phone_pending |
phone_required | activation_required | expired | revoked |
offline | verification_required | error
```

## 6. Client design dự kiến

### 6.1 Electron modules

```text
electron/license/
  license-manager.cjs
  license-api.cjs
  protected-license-store.cjs
  legacy-detector.cjs
  legacy-formulas.cjs
  hardware-profile.cjs
  license-contracts.cjs
```

Trách nhiệm:

- `license-manager`: state machine, in-flight deduplication, retry/offline policy, no concurrent activation/migration;
- `license-api`: HTTPS-only, timeouts, response size/schema validation, sanitized errors;
- `protected-license-store`: atomic encrypted token/profile write và corrupt-state handling;
- `legacy-detector`: Windows inventory + local evidence, không gọi portal/crawler;
- `legacy-formulas`: pure/versioned functions với golden fixtures;
- `hardware-profile`: collect, normalize, reject placeholder, hash raw signal trước khi return;
- `license-contracts`: allowlisted DTOs và enums.

Không chạy PowerShell bằng interpolated shell string. Windows inventory nên dùng một helper với fixed command/arguments, parse bounded JSON, deadline và output-size limit. Raw hardware values chỉ tồn tại trong main process đủ lâu để normalize/hash.

### 6.2 Protected files

```text
<userData>/security/
  device-private-key.bin
  device-public-key.pem
  license-token.bin
  device-profile.bin
  migration-state.json
```

- private key, token và device profile được `safeStorage` encrypt;
- `migration-state.json` chỉ chứa non-secret version/status/backoff IDs, không chứa raw key/phone/hardware;
- write bằng temp file cùng directory, flush phù hợp, atomic replace;
- update/reinstall không đụng thư mục này;
- uninstall data policy phải explicit, không âm thầm xóa customer state.

### 6.3 Hardware profile

Sáu signal dự kiến: `system_uuid`, `bios_serial`, `baseboard_serial`, `machine_guid`, `cpu_id`, `disk_serial`.

Mỗi field:

1. trim/Unicode normalize/casing theo field contract;
2. loại placeholder (`N/A`, `UNKNOWN`, `NONE`, OEM defaults, zero UUID);
3. hash domain-separated `SHA256(field_name + ":" + normalized_value)`;
4. không log raw hoặc full hash.

Baseline cần ít nhất ba signal hợp lệ. Recovery auto-pass khi match ít nhất 50% baseline với ngưỡng tối thiểu 3 matches; 2/6 không auto-rebind. Đây chỉ là recovery/risk signal. Normal verification vẫn là Ed25519 challenge proof.

### 6.4 IPC high-level

Renderer chỉ nhận:

```text
license.status()
license.initialize()
license.submitPhone(phone)
license.retry()
license.details()
```

`details()` trả masked phone/display key, expiry, device-bound state và presentation-safe error code. Không trả token, public-key material nếu UI không cần, hardware hashes, legacy candidates hoặc server challenge.

Main handler phải dùng `assertTrustedSender`, validate DTO, serialize calls qua một `LicenseManager`, và map lỗi sang allowlisted codes.

### 6.5 Renderer feature

```text
src/features/licensing/
  LicenseGate.tsx
  ActivationPage.tsx
  MigrationPage.tsx
  LicenseErrorPage.tsx
  PhoneForm.tsx
  types.ts
```

`LicenseGate` nằm ngoài component khởi tạo account/job/artifact lifecycle. Các screen reuse logo, typography, CSS token, button/card/input hiện có. Settings card dùng cùng high-level bridge và chỉ hiển thị masked data.

## 7. Server V2 design dự kiến

### 7.0 Audit source snapshot hiện có

`app.py` giữ `POST /verify-key` và thêm một `POST /verify-key-v2`. `auth.py` map sáu namespace riêng và `check_key()` vẫn trả nguyên nội dung `vip.txt` theo tool.

V2 snapshot hiện tại là code dành riêng cho **GSOFT**, không phải MIA: `verify_key_v2()` reject mọi `tool != "GSOFT"`, dùng paths dưới `GSOFT/` và tạo `KEYV2-...-phone`. Nó không chứng minh generator của MIA schema `KEY<29hex><phone>`.

Không reuse nguyên implementation này cho MIA vì:

- không có server challenge, Ed25519 signature hoặc signed license token;
- tin các hardware hash do client gửi sau khi chỉ kiểm tra shape 64-hex;
- canonical/display key phụ thuộc phone và chứa full phone, nên phone update có thể rotate key;
- recovery chọn candidate điểm cao nhất, không reject tie/ambiguity;
- baseline ba signal có thể pass với 2/3 matches vì chỉ kiểm ratio 50%;
- corrupt JSON được `_load_json()` biến thành `{}`, làm mất phân biệt corrupt với empty;
- migration ghi `vip.txt`, bindings JSON và migrations JSON qua nhiều atomic files nhưng không có một transaction chung; crash có thể để partial state;
- malformed expiry được legacy `_is_expired()` coi như chưa hết hạn;
- response trả full phone và full hardware profile;
- request list/string chưa có bounds ở application contract và snapshot không cho thấy rate limit/authentication;
- legacy `/verify-key` trả toàn key store, nên legacy key không thể là strong proof duy nhất.

Điểm có thể reuse về ý tưởng, sau khi viết lại trong MIA namespace: tool allowlist, process lock, atomic single-file replace, preserving old row trong grace period, hardware field allowlist và exact legacy candidate lookup.

Phần dưới đây vẫn là contract proposal. Không sửa trực tiếp hai file trong `Downloads`; cần authoritative server repository, deployment owner và regression environment.

### 7.1 Namespace/API

```text
POST /license/v2/challenge
POST /license/v2/migrate
POST /license/v2/verify
POST /license/v2/activate
POST /license/v2/recover
POST /license/v2/update-phone
```

Tất cả request bắt buộc `tool: "MIA"`. Production chỉ HTTPS; không fallback HTTP. Endpoint V2 không được mutate namespaces MIA2/MIA3/GBOT/IDQUICK/GSOFT.

Challenge phải random cryptographic, one-time, TTL ngắn, bound vào requested public key/fingerprint/tool/action và consumed transactionally. Signature verification phải precede bind/migrate.

Migration match priority chỉ exact:

1. exact legacy key candidate;
2. exact V1 `hash29` có duy nhất một active mapping;
3. V2 phone-aware exact match sau khi source được chứng minh;
4. verified adapter khác;
5. ambiguous/manual.

Không first-row/newest-expiry heuristic, substring hoặc fuzzy match.

### 7.2 Database proposal

`MIA/license.db` với SQLite transaction/WAL/backup policy được kiểm chứng trước production:

- `licenses`: immutable `license_id`, display key, status, expiry, phone/status, timestamps;
- `devices`: public key/fingerprint, hardware baseline, bind/revoke/recovery timestamps;
- `legacy_licenses`: raw preserved record, parsed fields/schema/confidence, expiry/metadata;
- `legacy_migrations`: unique legacy record to canonical license, source, timestamps, grace;
- `challenges`: hash/value metadata, expiry, consumed timestamp, action binding;
- `license_tokens`: token ID/hash, expiry/offline lease/revocation;
- `audit_log`: actor/action/result/correlation IDs, no secrets.

Migration transaction must preserve original expiry and metadata, enforce one legacy record migrated once, bind the new public key and create token atomically. Failure rolls back; it must not mark local/server migration complete partially.

`MIA/vip.txt` remains unchanged/read-compatible throughout pilot and grace period.

### 7.3 Offline lease

Offline duration is a business policy blocker. Token must be server-signed and contain immutable license/device IDs, issued/expiry, `offline_valid_until`, audience/tool and token ID. Network error may use an unexpired lease; expired/revoked/invalid signature must not. Timeout must never delete token or legacy evidence.

## 8. Phone/new customer policy

Phone is business/recovery metadata, not sole security proof.

- New customer: no valid V2 token **and** legacy result is definitive `no_match` -> Phone Form.
- Legacy V2 with proven phone -> migrate without asking again.
- Exact legacy V1 without phone -> migrate first; use `phone_status=pending` and apply the approved legacy-phone UX.
- Ambiguous legacy -> supplemental/manual verification, not automatic new-user activation.
- Dummy values such as `0000000000` are forbidden in defaults, tests that cross production paths, generated profile and requests.
- Updating phone does not rotate `license_id`, `device_id` or key pair.

The repository search found no current default/dummy license phone implementation. Numeric strings in visual tests are tax-account usernames, not license phone profiles.

## 9. Logging and privacy

Reuse `electron/app-logger.cjs` structured logs and extend sanitizer before V2 rollout.

Allowlisted events:

- `license_init_started`
- `legacy_detection_completed`
- `legacy_migration_success`
- `legacy_migration_ambiguous`
- `license_verify_success`
- `license_verify_failed`
- `phone_profile_updated`
- `device_recovery_success`
- `device_recovery_failed`

Fields may include state, schema, candidate count, attempt, duration, masked IDs and safe error code. Never log full phone/key/token/challenge/signature/public key, raw serial, full hardware hash, private key or raw legacy row.

The current generic sensitive-key regex treats `key` mainly in API-key contexts and is not sufficient by itself for legacy/canonical license values; add license-specific field allowlisting and masking before new events.

## 10. Test gates

### 10.1 Client unit/integration

- exact V1 golden fixture and casing/length;
- Windows one/multiple/reordered disk candidates, missing serial and size filter;
- V2 candidate only after source-proven phone rule;
- initialize ordering: token -> legacy -> phone;
- V1 no-phone migrates without new-key flow;
- V2 phone migrates without Phone Form;
- ambiguity never auto-claims;
- no legacy opens Phone Form;
- challenge replay/expiry/wrong key/signature failures;
- stable identity/license across restart and update;
- atomic store failure keeps last valid state;
- network failure respects signed offline lease;
- 6/6 through 3/6 recovery and 2/6 rejection;
- copied security directory fails on a second Windows device;
- phone update preserves canonical IDs;
- no HĐĐT data/preferences mutation.

### 10.2 Server isolation

Regression matrix must exercise `/verify-key` for MIA, MIA2, MIA3, GBOT, IDQUICK and GSOFT before and after V2 deploy. V2 migration/activate/recover must prove it writes only MIA tables/files.

### 10.3 UI/packaging

Playwright states: checking, migrating, phone required, activation required, active/settings, expired, revoked/offline/error. Test Electron resize and existing HDDT/XML/results visuals.

Packaged Windows matrix:

1. old installed MIA with legacy evidence;
2. install V2 build over it;
3. silent migration and restart;
4. install another update;
5. same identity/license and intact invoice/account data;
6. corrupt/network/reboot cases;
7. open generated installer only after code-signing gate is resolved.

Current release report still blocks production claims on Windows code signing and clean-VM installer/update coverage.

## 11. Feature flag và rollout

Use a main-process configuration such as `MIA_LICENSE_V2_ENABLED`, never a renderer `VITE_*` secret. Feature flag disabled means existing desktop startup is unchanged.

Rollout:

1. audit/server data analyzer;
2. server V2 alongside untouched legacy endpoint;
3. client V2 behind disabled flag;
4. internal fixtures and clean Windows VM;
5. pilot verified V1/V2 cohorts;
6. measure silent/ambiguous/manual/error rates;
7. general rollout only after acceptance gates;
8. MIA-only legacy grace/sunset after measured coverage.

## 12. Rollback plan

- Do not delete or rewrite `MIA/vip.txt`.
- Do not mark a legacy record migrated until server transaction commits.
- Keep `legacy_migrated_at` and `legacy_grace_until`; pilot rollback can disable V2 while legacy MIA remains accepted during grace.
- Client feature flag can return startup to current behavior without deleting protected token/profile or customer data.
- New client must tolerate V2 server unavailable by honoring the approved offline lease or showing retry; it must not create a new phone/license identity.
- Database deployment requires backup, migration version and restore rehearsal.
- Server rollback is MIA V2 only; no shared-tool route/config mutation.

## 13. Đầu vào còn thiếu trước khi triển khai production

Client MIA V2 và server package độc lập đã được triển khai, test cục bộ và giữ sau feature flag. Các đầu vào dưới đây không chặn implementation, nhưng vẫn chặn việc bật production:

1. authoritative key-server repository/revision, dependency/config, deployment procedure và tests; hai source snapshot đã có nhưng không kèm provenance/version history;
2. canonical server `MIA/vip.txt` được đối chiếu SHA/count ngay trước rollout; controlled snapshot hiện tại đã được phân tích;
3. source path/golden fixtures cho V2 phone, full-hash và MAK generation;
4. sanitized samples cho malformed/custom rows;
5. production/staging HTTPS base URL và certificate/deployment ownership;
6. business policy cho offline lease, legacy phone pending, device count/rebind và expiry;
7. authorized staging admin/test licenses cho migration tests;
8. code-signing certificate và clean Windows VM cho packaged rollout.

## 14. Kết quả Stage 1

- Legacy schemas đã phân loại. Pure V1 formula và ordered candidate builder đã được triển khai tại `electron/license/legacy-formulas.cjs` với golden/regression tests; module chưa được nối vào startup hoặc server.
- Source snapshot/server functions đã được audit, nhưng exact production revision và generator path của MIA V2/V3/MAK vẫn chưa được chứng minh.
- Snapshot schema distribution đã đo: V1/V2 observed 98,43%; conservative unique-hash estimate 95,19%; không trình bày estimate này như actual migration rate.
- Trường hợp manual bắt buộc: ambiguous hash mapping, unsupported/unproven schema, insufficient device proof, revoked/expired policy, malformed record và recovery dưới threshold.
- Client files/modules, server APIs, database schema, UI flow, data path, test gates và rollback đã được thiết kế.
- Production behavior giữ nguyên trong Stage 1.

## 15. Implementation MIA License V2

Taxsoft `develop` tại commit `815ba9b26a73b72789dcd9ba9a1b426e6f876fae` được dùng làm reference contract cho kết nối, hardware profile và recovery threshold; namespace `GSOFT`, HTTP endpoint và mô hình phone-first của Taxsoft không được sao chép sang MIA.

Phần đã triển khai:

- Electron `LicenseManager` chạy theo thứ tự token V2 -> exact legacy migration -> Phone Form;
- hardware profile sáu signal đã hash theo domain, exact MIA V1 disk candidates và observed V2 phone candidates;
- Ed25519 one-time challenge/response, token rotation, offline lease, device binding và recovery 3/6 trở lên;
- profile/token được mã hóa bằng Electron `safeStorage`, ghi atomic và không expose raw token/private key qua preload;
- React license gate cho migration, Phone Form, activation, retry và settings;
- package deploy độc lập `server_mia_license_v2/` với SQLite schema, import legacy, API routes, smoke test và tài liệu deploy;
- mọi server operation bắt buộc `tool=MIA`; các namespace MIA2/MIA3/GBOT/IDQUICK/GSOFT không bị mutate;
- feature flag `MIA_LICENSE_V2_ENABLED` mặc định tắt, nên production startup hiện tại không đổi cho đến khi rollout được phê duyệt.

Trạng thái: **implementation ready; production deployment pending**. Còn cần staging HTTPS URL, secret/certificate ownership, canonical legacy snapshot trước rollout, authorized test license, code signing và clean Windows VM/NSIS upgrade verification.
