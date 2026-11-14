# Current local device test

The isolated `campus-live` coordinator runs upstream control-plane commit
`96bfe6d781b080504b9fb7cb2b303f18c64394f7` at
`https://10.4.146.76:8443`. PostgreSQL is internal-only. The gateway uses its
local CA, which is included in each device package and used only by Campus.
No global certificate installation is needed.

## Проверка на Mac

1. Передайте `test-kit/exports/mac-connect.zip` на Apple Silicon Mac и распакуйте.
2. Установите и откройте Docker Desktop для Apple Silicon; завершите его
   первоначальную настройку. Подключите ноутбук к питанию.
3. Подключите Mac к той же локальной сети, что и сервер.
4. Откройте `Connect.command` в распакованной папке и оставьте окно открытым.
   Адрес, сертификат и приглашение уже подготовлены.
5. Дождитесь успешной проверки и подключения. На сервере подтвердите свежий
   `last_seen_at` и состояние `ready` именно этого устройства.
6. Нажмите Ctrl+C для остановки. Повторный запуск должен использовать
   сохранённую регистрацию, не расходуя новое приглашение.

При штатном предупреждении macOS разрешите запуск конкретного проверенного
файла через обычное меню открытия/«Конфиденциальность и безопасность».
Не отключайте системную защиту. Если проверка не проходит, сохраните текст
ошибки; не публикуйте `invite.txt` или файлы регистрации.

## Проверка на Windows 11 с AMD и RTX 5050

1. Передайте полный пакет `windows-connect`, распакуйте его и откройте Docker
   Desktop с Linux-контейнерами. Нужен установленный драйвер NVIDIA с поддержкой
   этой видеокарты; обнаружение названия карты ещё не подтверждает CUDA.
2. Подключите питание и ту же локальную сеть. Откройте `Connect.cmd`.
3. Проверьте показанные CPU, RAM, название NVIDIA, VRAM и драйвер.
4. На вопрос о задачах на GPU ответьте `y`, если разрешаете их. Устройство
   предложит GPU серверу только после настоящего вычисления в CUDA-контейнере.
5. Подтвердите на сервере свежий статус `ready`, затем проверьте Ctrl+C и
   повторный запуск. Окно остаётся открытым во время участия.

Пакеты с приглашениями предназначены каждый для своего устройства. Текущие
приглашения действуют до **2026-11-15 18:27 по Asia/Shanghai**. После успешной
регистрации используется сохранённая учётная запись; истечение приглашения
не требует повторной регистрации. Для первого подключения позже нужен новый
пакет/приглашение.

## Operator status and restart

Runtime configuration, CA, owner credential and invitations are private under
`%LOCALAPPDATA%/Campus/server-96bfe6d/`; never commit or print their contents.
The unmodified upstream snapshot is under `.git/campus-runner/control-plane-96bfe6d/`.
From the repository root in PowerShell:

```powershell
$campusRuntime = Join-Path $env:LOCALAPPDATA 'Campus/server-96bfe6d'
$campusSource = (Resolve-Path .git/campus-runner/control-plane-96bfe6d).Path
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
A DHCP/network address change needs an updated TLS site and device packages.
Peer reachability on eduroam is unproven until the other laptop connects;
client isolation can require using a shared private router/hotspot instead.

## Evidence and limits

Strict-CA health/protocol checks and real Go enrollment/heartbeat passed.
The new native Windows `connect` path also passed real CPU preflight, saved
identity reconnect, a fresh ready heartbeat and explicit local Stop. Native
Mac Finder/quarantine and physical RTX 5050 CUDA remain target-device tests.

This handoff verifies device connection. The upstream assignment still lacks
the agreed relative task deadline and detailed sandbox policy. The runner
rejects incomplete assignments before execution. Distributed workloads,
two-device speedups and GPU checkpoint recovery are separate remaining gates;
see [the protocol record](../runner/PROTOCOL.md).

The first Mac launch reported `zsh: killed` before Campus output. Docker was
running in the supplied screenshot. The original ARM64 binary's built-in
ad-hoc page hashes are valid; this does not establish the cause on that Mac.
Collect these local diagnostics without reading the private invitation:

```sh
cd ~/Downloads/mac-connect
shasum -a 256 campus
codesign --verify --verbose=2 campus
xattr -p com.apple.quarantine campus
sh ./Connect.command --check-only
```

Expected `campus` SHA-256:
`810796d6cc11d4f8a7497e60a860d8282f2bfa61647b78b57ed09f433cf28cad`.
Do not re-sign/modify the binary: that invalidates the trusted launcher pin.
