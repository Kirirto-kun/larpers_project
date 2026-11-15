# Current distributed-project release: R8

The LAN service is running the distributed-project implementation at
[Campus](https://10.4.146.76:8443). The canonical private startup override keeps
the existing database, artifacts, TLS CA and all earlier download routes.
PostgreSQL remains internal-only. Earlier entries below are historical.

Computing-device starters:

- [Windows Connect.cmd](https://10.4.146.76:8443/downloads/runner-20261115-r8/Connect.cmd)
- [macOS/Linux Connect.command](https://10.4.146.76:8443/downloads/runner-20261115-r8/Connect.command)

Developer submission uses the separate [CLI ZIP downloads and guide](../docs/DISTRIBUTED_PROJECTS.md).
After sign-in, run `campus run --wait --to results/run-001` from a prepared
project. The [Fashion-MNIST project ZIP](https://10.4.146.76:8443/downloads/runner-20261115-r8/fashion-mnist-project.zip)
contains source/preparation scripts, without dataset bytes. Docker remains a
manual prerequisite for contributing devices; invitations are entered separately.

R8 fixes a confirmed Windows Docker-bind rename failure by using read-only
state handles with delete sharing. The real Linux bind-mount regression and
full Go suite passed. Existing Mac computing clients can continue; use the R8
developer CLI for the new project submission command.

Strict-CA HTTPS checks verified complete non-image download hashes, all three
image Range responses, starter attachment headers and private-path 404s.
The unchanged image archives were fully validated by the release builder and
reused as hardlinks. No invitation or credential is included.

[SHA256SUMS](https://10.4.146.76:8443/downloads/runner-20261115-r8/SHA256SUMS) SHA-256: `564339ae4c5a810086fbb8a33f57a3aab9c8dd0fccb938f471e2a2712aa656df`.
Catalog SHA-256: `a63230c3116c601bfb2bf988508b5d62968c75203b930ed408cb0b83c47f26fb`.
Windows native SHA-256: `b3001ad53cf57148e718be38ed89d929556a07c4de1963852f13355dd042eca8`.

One actual job completed 24/24 tasks across the main Windows computer, RTX 5050
and Mac. All 60,000 training / 10,000 test images matched an independent
numerical reference; see the [verification record](../docs/DISTRIBUTED_VERIFICATION.md).
The owner restarted RTX with R8. Nine measured jobs completed 216/216 tasks;
an active-worker stop recovered both unfinished tasks on RTX. A separate R8
CUDA job matched all 65,536 values against an independent CPU reference.
The small classifier showed no speed gain over the RTX laptop alone; see the
verification record for complete timings and limitations. Original Mac settings
were restored, and the root-owned main test worker was stopped cleanly.

---

# Current LAN handoff

## Windows setup update: R4

Current Windows file: `bootstrap/test-kit/public/runner-20261115-r4/Connect.cmd`,
33154 bytes, SHA-256
`c914c99bef4fa95dd30f5ac36a43fd09c7f25fbf9d0c9deecd16a93335b21fee`.
It is hosted read-only at
[R4 Connect.cmd](https://10.4.146.76:8443/downloads/runner-20261115-r4/Connect.cmd).
Docker Desktop is installed manually. The starter checks/setup WSL2 and,
after GPU permission, the verified NVIDIA5050 Laptop Windows driver. Confirm
the normal UAC/vendor windows. If restart is required, reboot and run the
same file again; invitations are requested only after readiness/preflight.

R4 actual cached Windows startup detected ready WSL2.5.10 without changing it,
passed CPU preflight, restored identity and reached ready at
`2026-11-14T16:10:24.117455Z`. Owner Stop exited0. Missing-component branches
were tested with17 WSL,58 NVIDIA and7 orchestration cases. The combined public
and orchestration run passed54 cases initially; the6 changed argv expectations were
updated and reran green. Native32/64-bit system tool selection was checked.
Strict-CA live catalog/starters/Range/private-file404 and secret scans passed.
Real missing-component installation/UAC/reboot still needs the target laptop;
this host's running Docker/WSL and absent NVIDIA hardware were preserved.

R3 and earlier releases below remain recorded alternatives; distribute R4 for
the automatic Windows prerequisite flow. The catalog/native clients/images
are unchanged. Large images reused verified hardlink identities/sizes rather
than another multi-GiB rehash. No invitation is embedded in these files.

The isolated `campus-live` coordinator runs upstream control-plane commit
`96bfe6d781b080504b9fb7cb2b303f18c64394f7` with the restricted Mac diagnostic
overlay at [https://10.4.146.76:8443](https://10.4.146.76:8443).
PostgreSQL is internal-only. The gateway uses its local CA; its public certificate
is included in starters/packages and trusted only by Campus. No global
certificate installation is needed. This address has been reached by the
physical Mac on the same LAN; it is not an Internet-public download host.

## Public starter release status

The final actual R3 Windows cached launch passed CPU preflight and restored
the device to ready (server heartbeat `2026-11-14T12:48:32.469569Z`). Local
`install --stop` and the visible CMD both exited 0. CUDA was absent from the
CPU-only cache. The startup files can be shared separately from invitations.

The current R3 release is `bootstrap/test-kit/public/runner-20261114-r3`, served
read-only at
[the R3 download directory](https://10.4.146.76:8443/downloads/runner-20261114-r3/).
The earlier R1/R2 directories remain available as recorded releases; forward
the R3 starter files below.

| R3 public file | Bytes | SHA-256 |
| --- | ---: | --- |
| [Connect.cmd](https://10.4.146.76:8443/downloads/runner-20261114-r3/Connect.cmd) | 9490 | `873be9cd6c3d4bdc6677f7eb0a92c5622126c529bfab06756372beb0b0bcc5b5` |
| [Connect.command](https://10.4.146.76:8443/downloads/runner-20261114-r3/Connect.command) | 5949 | `cc785a315da0285d0ea234d45bef8ebe369084b94d70e385e80ba64b288b12dd` |
| [Connect.ps1](https://10.4.146.76:8443/downloads/runner-20261114-r3/Connect.ps1) | 8356 | `59c179f1be2851d4687be3e3fc70049aa1afd96a2631d6778333d1485cfd72d4` |

R3 catalog SHA-256:
`9758a529ef5e3f119b590294e20e7730d8aafcefbca7a7bc6e8eca5efc7049d3`.
R3 `SHA256SUMS` SHA-256:
`4958ca8b83c8f0684843ecb2c977db50e68411c8f2f3d9d7758594fa91ffbf31`.
The catalog and three already-verified image archives are unchanged. R3 reused
their hardlinks after identity/size checks; all 18 non-image files were freshly
hashed. This was not another complete read of all image archives.

The actual R3 Windows CMD path launched from inherited PowerShell 7, reused the
verified client and CPU environment, passed CPU preflight, restored the saved
identity and entered Serving. The preceding R2 run had downloaded that client
and CPU environment from an empty cache and reached ready. No CUDA archive
object was present in that CPU-only cache. The latest targeted public suite
passed **53 tests in 52.19 seconds**, including two non-administrator cache runs,
protected owner DACLs without a second client download, CIM discovery, parent
module-path restoration and exit codes 0/1/7/130. R3 fixes repeat-cache ACL
handling without requiring the `SeSecurityPrivilege` privilege.

The preceding R1 directory was verified through strict TLS at
[the R1 download directory](https://10.4.146.76:8443/downloads/runner-20261114-r1/).
All six client targets answered HEAD, all three starter downloads matched their
local bytes, Range requests returned 206, and invitation/owner/runtime paths
returned 404. Public files contain no enrollment secrets. The gateway serves
only the release directory read-only, not the surrounding private runtime.

Forward only `Connect.cmd` for Windows or `Connect.command` for macOS/Linux
through the trusted handoff channel. They are kilobyte-sized; the large approved
client/environment assets stay on the server. `Connect.ps1` is an optional
advanced Windows entry point. The starter detects native OS/architecture and
verifies exact size/SHA-256 before running a cached client. The native client
then checks CPU/NVIDIA/Docker, selects the matching environment and asks
separately for GPU consent and, for a new device, its private invitation.

CPU archives are 198349093 bytes for arm64 and 282383826 bytes for amd64; the
optional amd64 CUDA archive is 4175747216 bytes. CUDA is downloaded only after
explicit GPU consent. Interrupted environment downloads resume using checked
HTTP Range; verified payloads are cached by hash and checked before reuse.
The small catalog is still fetched and pinned on each install invocation.

Native clients exist for Windows/macOS/Linux on amd64/arm64. Docker Desktop must
already be installed and initialized on Windows/macOS; Windows ARM support is
currently Early Access. Linux needs a healthy local Docker runtime. No ordinary
phone, Apple Metal or ARM CUDA target is offered. Six builds do not mean six
physical runtime acceptances. See [public release details](public/README.md) and
[CLI options](../runner/CLI.md).

## Проверка на Mac

1. Передайте проверенный публичный `Connect.command` на Mac. Приглашение для
   нового устройства передайте отдельно и не добавляйте его в этот файл.
2. Установите и откройте Docker Desktop для Apple Silicon; завершите его
   первоначальную настройку. Подключите ноутбук к питанию.
3. Подключите Mac к той же локальной сети, что и сервер.
4. Откройте Terminal и выполните `sh ~/Downloads/Connect.command`
   (если сохранили в Downloads). Для другой папки введите `sh `, перетащите
   `Connect.command` в Terminal и нажмите Enter. Оставьте окно открытым.
   Адрес, публичный сертификат и контрольные суммы уже подготовлены. Клиент и
   CPU-окружение загрузятся автоматически и сохранятся в проверяемом кэше.
5. После успешной проверки введите отдельное приглашение, если это первое
   подключение. При сохранённой регистрации новое приглашение не требуется.
   Дождитесь подключения. На сервере подтвердите свежий
   `last_seen_at` и состояние `ready` именно этого устройства.
6. Нажмите Ctrl+C для остановки. Повторный запуск должен использовать
   сохранённую регистрацию, не расходуя новое приглашение.

При штатном предупреждении macOS разрешите запуск конкретного проверенного
файла через обычное меню открытия/«Конфиденциальность и безопасность».
Не отключайте системную защиту. Если проверка не проходит, сохраните текст
ошибки; не публикуйте `invite.txt` или файлы регистрации.

## Проверка на Windows 11 с AMD и RTX 5050

1. Передайте проверенный публичный `Connect.cmd` и откройте Docker Desktop с
   Linux-контейнерами. Для GPU нужен установленный драйвер NVIDIA с поддержкой
   этой видеокарты; обнаружение названия карты ещё не подтверждает CUDA.
2. Подключите питание и ту же локальную сеть. Откройте `Connect.cmd`.
3. Проверьте показанные CPU, RAM, название NVIDIA, VRAM и драйвер.
4. На отдельный вопрос о загрузке CUDA и задачах на GPU ответьте `y`, если
   разрешаете их; по умолчанию выбран CPU. CUDA-архив не загружается без
   согласия. Устройство предложит GPU серверу только после настоящего вычисления
   в CUDA-контейнере. Обнаружение названия карты не заменяет эту проверку.
5. Для нового устройства введите отдельно переданное приглашение после
   проверки. Подтвердите на сервере свежий статус `ready`, затем проверьте Ctrl+C и
   повторный запуск. Окно остаётся открытым во время участия.

Для первого подключения каждого нового устройства нужно отдельное приглашение
администратора. Публичный стартер можно пересылать; приглашение и файлы
регистрации остаются приватными. После успешной регистрации используется
сохранённая учётная запись; истечение приглашения не требует повторной
регистрации. Во время участия оставляйте окно открытым; Ctrl+C останавливает
процесс. Локальные `--stop` и `--drain` описаны в CLI.

## Alternative private offline packages

The existing `bootstrap/test-kit/exports/mac-connect.zip` and Windows device ZIP
remain usable when transferring the full client/images offline is preferable.
Keep their files together and run their own `Connect.command`/`Connect.cmd`.
They use `campus connect`, not the public download path. Packages containing
invitations are private to their devices and must not be published. The recorded
packaged invitations expire at **2026-11-15 18:27 Asia/Shanghai**; a later first
enrollment requires a new invitation. Saved identities reconnect independently
of that invitation's expiry.

## Operator status and restart

Runtime configuration, CA, owner credential and invitations are private under
`%LOCALAPPDATA%/Campus/server-96bfe6d/`; never commit or print their contents.
The unmodified upstream snapshot is under `.git/campus-runner/control-plane-96bfe6d/`.
The current API build uses the separate compatibility staging copy below. Its
runtime override mounts `bootstrap/control_plane_compat/mac-catalog.json` and
`mac-policy.json` read-only, with `CATALOG_FILE=/catalog/mac-catalog.json` and
`CAMPUS_DIAGNOSTIC_WORKER_POLICY=/catalog/mac-policy.json`. Keep that override
when restarting; the database remains the existing `campus-live` database.
From the repository root in PowerShell:

```powershell
$campusRuntime = Join-Path $env:LOCALAPPDATA 'Campus/server-96bfe6d'
$campusSource = (Resolve-Path .git/campus-runner/control-plane-96bfe6d-compat).Path
$campusCompose = @('compose', '--project-name', 'campus-live',
    '--project-directory', (Join-Path $campusSource 'infra'),
    '--env-file', (Join-Path $campusRuntime 'runtime.env'),
    '-f', (Join-Path $campusSource 'infra/compose.yaml'),
    '-f', (Join-Path $campusRuntime 'live.override.yaml'))
docker @campusCompose ps
# Start existing services after Docker Desktop is ready:
docker @campusCompose up -d
```

All three services use `restart: unless-stopped`. Keep this computer awake
and connected. The current address belongs to Wi-Fi, not the VPN adapter.
A DHCP/network address change needs an updated TLS site and newly pinned
public starters/catalogs or private device packages.
Peer reachability on this eduroam connection passed with the physical M2 Mac.
Other networks can still isolate clients and require a shared private
router/hotspot instead.

## Evidence and limits

Strict-CA health/protocol checks and real Go enrollment/heartbeat passed.
The new native Windows `connect` path also passed real CPU preflight, saved
identity reconnect, a fresh ready heartbeat and explicit local Stop. The
physical M2 Mac passed native ARM64 CPU preflight, saved identity reconnect
and strict-TLS communication with this server. The owner API confirmed
`MacBook Apple Silicon`, `ready`, darwin/arm64, 8 logical CPUs and
`last_seen_at=2026-11-14T11:24:04.648714Z`. Physical Ctrl+C then returned the
expected exit 130; the user restarted the same launcher and restored the
saved connection successfully. Physical RTX 5050 CUDA remains to check.

The same physical Mac later completed job
`f62d56ff-b5d0-4866-aa5c-d1e0a24f00a4`: a native Linux/aarch64 Python task fitted
a quadratic model to 250,000 samples and returned `model.json` (281 bytes),
SHA-256 `eb57995ae2440f334c6d8c1c8e9ef4ed41b5ed3ef29e759fa85ee43db93482f7`.
The authenticated download passed exact size/hash and an independent
exact-rational coefficient/MSE check. Server verification is `structural`.
The calculation reported 447.717 ms; this is a computation diagnostic, not a
performance benchmark. The Mac returned to ready with zero active attempts.

This uses only the explicit Mac/node/image/adapter-allowlisted CPU overlay:
1 CPU, 256 MiB RAM, 64 PIDs, 34 MiB tmpfs quotas, 60-second hard bound and no
network or GPU. Ordinary assignments still fail closed without the agreed
relative deadline and detailed sandbox policy. General job compatibility,
two-device speedups and GPU checkpoint recovery remain separate gates. See
[the physical result](control_plane_compat/RESULT.md) and
[the protocol record](../runner/PROTOCOL.md).

The first Mac Finder launch reported `zsh: killed` before Campus output.
The transferred binary hash matched, native `codesign` verification passed,
and an Edge quarantine attribute was present. Running the launcher through
`sh` subsequently passed preflight and connected successfully. The original
Finder kill's cause is not established. Use the verified `sh` entry point;
the diagnostic commands, if needed again, do not read the private invitation:

```sh
cd ~/Downloads/mac-connect
shasum -a 256 campus
codesign --verify --verbose=2 campus
xattr -p com.apple.quarantine campus
sh ./Connect.command --check-only
```

Expected `campus` SHA-256 for that recorded private Mac package only:
`810796d6cc11d4f8a7497e60a860d8282f2bfa61647b78b57ed09f433cf28cad`.
Do not re-sign/modify the binary: that invalidates the trusted launcher pin.

## Local package records

The private ZIPs are under `bootstrap/test-kit/exports/` (ignored by Git):

| Target | Bytes | ZIP SHA-256 |
| --- | ---: | --- |
| Apple Silicon Mac | 208331587 | `8789a2c7bb7b711aa9be9bda6ebc14f9d52fe9a3d5929d1f43a683ee26110c07` |
| Windows AMD64 CPU/CUDA | 4468742936 | `c68c0eda2f85276d8cf34cb67fa25ff7b18b523215424a15ac39024c6902c134` |

Both ZIPs passed full payload hashes, launcher pins, native Go build metadata,
ZIP CRCs and owner-only ACL verification. Mac executable Unix modes are
preserved; the Windows package uses Zip64. Neither record proves physical
RTX 5050 execution.
