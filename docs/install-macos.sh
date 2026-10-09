#!/bin/bash
# Install a verified Apple Silicon release at a stable path. Never grants TCC permissions.
set -euo pipefail

die() { printf '%s\n' "$*" >&2; exit 1; }
dry_run=0
desktop_link=0
for option in "$@"; do
  case "$option" in
    --dry-run) dry_run=1 ;;
    --desktop-link) desktop_link=1 ;;
    *) die "Unknown option: $option (supported: --dry-run, --desktop-link)" ;;
  esac
done
[[ $(/usr/bin/uname -s) == Darwin ]] || die 'This installer must run on macOS.'
chip=$(/usr/bin/uname -m)
if [[ "$chip" != arm64 ]]; then
  [[ $(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true) == 1 ]] || \
    die '当前安装包仅支持 Apple 芯片。Intel Mac 请先停止，不要安装错误架构的包。'
fi
[[ -n "${HOME:-}" && -d "$HOME" ]] || die 'Cannot locate the current user home directory.'

temp_parent=$(cd "${TMPDIR:-/tmp}" && pwd -P)
work=$(/usr/bin/mktemp -d "$temp_parent/codex-whip-install.XXXXXX")
mount_point="$work/mount"
stage=''
install_parent=''
mounted=0
cleanup() {
  if [[ "$mounted" == 1 ]]; then /usr/bin/hdiutil detach "$mount_point" >/dev/null 2>&1 || true; fi
  if [[ -n "$stage" && -n "$install_parent" ]]; then
    case "$stage" in "$install_parent"/.codex-whip-stage.*) /bin/rm -rf -- "$stage" ;; esac
  fi
  case "$work" in "$temp_parent"/codex-whip-install.*) /bin/rm -rf -- "$work" ;; esac
}
trap cleanup EXIT

download() {
  /usr/bin/curl --fail --location --show-error --proto '=https' --proto-redir '=https' \
    --retry 3 --connect-timeout 20 --max-time 1800 --output "$2" "$1"
}

# /releases/latest usually points to Windows. Select a published Mac release instead.
metadata=''
for page in 1 2 3 4 5; do
  download "https://api.github.com/repos/2050246567zb-coder/codex-whip-desktop/releases?per_page=100&page=$page" "$work/releases.json"
  metadata=$(/usr/bin/osascript -l JavaScript - "$work/releases.json" <<'JXA'
function selectRelease(releases) {
  if (!Array.isArray(releases)) throw new Error('Invalid GitHub release response');
  const candidates = releases.filter(function (r) {
    return !r.draft && !r.prerelease && /^v\d+\.\d+\.\d+-macos$/.test(r.tag_name || '') && r.published_at;
  }).sort(function (a, b) { return Date.parse(b.published_at) - Date.parse(a.published_at); });
  if (!candidates.length) return 'CONTINUE';
  const release = candidates[0];
  const version = release.tag_name.slice(1, -6);
  const filename = 'CodexWhip-' + version + '-Apple-Silicon.dmg';
  const checksums = 'SHA256SUMS-' + version + '-macos.txt';
  const prefix = 'https://github.com/2050246567zb-coder/codex-whip-desktop/releases/download/' + release.tag_name + '/';
  function asset(name) {
    const matches = (release.assets || []).filter(function (a) { return a.name === name; });
    if (matches.length !== 1 || matches[0].state !== 'uploaded' || matches[0].size <= 0 || matches[0].browser_download_url !== prefix + name)
      throw new Error('Latest Mac release has missing or invalid asset: ' + name);
    return matches[0].browser_download_url;
  }
  return [version, filename, asset(filename), asset(checksums)].join('\n');
}
function run(argv) {
  ObjC.import('Foundation');
  const text = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null);
  if (!text) throw new Error('Cannot read release metadata');
  return selectRelease(JSON.parse(ObjC.unwrap(text)));
}
JXA
  )
  [[ "$metadata" == CONTINUE ]] || break
done
[[ -n "$metadata" && "$metadata" != CONTINUE ]] || die '没有找到可安装的正式 Mac 版，请查看 GitHub Releases。'
version=$(printf '%s\n' "$metadata" | /usr/bin/sed -n '1p')
package_name=$(printf '%s\n' "$metadata" | /usr/bin/sed -n '2p')
package_url=$(printf '%s\n' "$metadata" | /usr/bin/sed -n '3p')
checksum_url=$(printf '%s\n' "$metadata" | /usr/bin/sed -n '4p')

install_dir=/Applications
if [[ ! -w "$install_dir" ]]; then
  [[ ! -e "$install_dir/CodexWhip.app" ]] || die '已有 /Applications/CodexWhip.app，但当前用户不能更新它。请由 Codex 引导你确认管理员权限，勿另装一个副本。'
  install_dir="$HOME/Applications"
  if [[ "$dry_run" == 0 ]]; then /bin/mkdir -p "$install_dir"; fi
fi
destination="$install_dir/CodexWhip.app"
printf 'Selected macOS %s (Apple Silicon)\nInstall location: %s\n' "$version" "$destination"
if [[ "$dry_run" == 1 ]]; then
  printf 'DMG: %s\nSHA256: %s\nDry run: no app downloaded, installed or launched.\n' "$package_url" "$checksum_url"
  exit 0
fi
/usr/bin/pgrep -x CodexWhip >/dev/null && die '请先完全退出所有 CodexWhip，再重新执行安装。'

download "$package_url" "$work/$package_name"
download "$checksum_url" "$work/SHA256SUMS.txt"
expected=$(/usr/bin/awk -v name="$package_name" '$2 == name || $2 == "*"name { print $1 }' "$work/SHA256SUMS.txt")
[[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die '校验文件缺少唯一的安装包 SHA-256，请停止安装。'
expected=$(printf '%s' "$expected" | /usr/bin/tr 'A-F' 'a-f')
actual=$(/usr/bin/shasum -a 256 "$work/$package_name" | /usr/bin/awk '{print $1}')
[[ "$actual" == "$expected" ]] || die '安装包 SHA-256 不匹配，请停止安装。'

/bin/mkdir "$mount_point"
/usr/bin/hdiutil attach "$work/$package_name" -readonly -nobrowse -mountpoint "$mount_point" >/dev/null
mounted=1
source_app="$mount_point/CodexWhip.app"
[[ -d "$source_app" && ! -L "$source_app" ]] || die '安装包内缺少正常的 CodexWhip.app。'
[[ $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$source_app/Contents/Info.plist") == com.codexwhip.desktop ]] || die '安装包的应用身份不匹配。'
[[ $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$source_app/Contents/Info.plist") == "$version" ]] || die '应用版本与发布版本不匹配。'
/usr/bin/codesign --verify --deep --strict "$source_app"

install_parent=$(cd "$install_dir" && pwd -P)
destination="$install_parent/CodexWhip.app"
[[ ! -L "$destination" ]] || die '安装目标是符号链接，请先由用户确认，不自动覆盖。'
stage=$(/usr/bin/mktemp -d "$install_parent/.codex-whip-stage.XXXXXX")
/usr/bin/ditto "$source_app" "$stage/CodexWhip.app"
/usr/bin/codesign --verify --deep --strict "$stage/CodexWhip.app"
backup=''
if [[ -e "$destination" ]]; then
  [[ -d "$destination" ]] || die '安装目标不是应用目录，请停止安装。'
  backup_parent="$HOME/Library/Application Support/CodexWhip/installer-backups"
  /bin/mkdir -p "$backup_parent"
  backup="$backup_parent/CodexWhip-$(/bin/date +%Y%m%d-%H%M%S)-$$.app"
  /bin/mv "$destination" "$backup"
fi
if ! /bin/mv "$stage/CodexWhip.app" "$destination"; then
  if [[ -n "$backup" ]]; then /bin/mv "$backup" "$destination"; fi
  die '安装失败；已尝试恢复原有软件。'
fi
/usr/bin/hdiutil detach "$mount_point" >/dev/null
mounted=0
if [[ "$desktop_link" == 1 ]]; then
  shortcut="$HOME/Desktop/Codex Whip.app"
  if [[ -d "$HOME/Desktop" && ! -e "$shortcut" && ! -L "$shortcut" ]]; then
    /bin/ln -s "$destination" "$shortcut" || printf '桌面快捷方式创建失败（可能缺少桌面访问权限），应用已安装，请从应用程序目录打开。\n'
  else
    printf '桌面快捷方式未创建：桌面不可用或已有同名文件，未覆盖。\n'
  fi
fi
printf '\n文件安装完成：%s\n' "$destination"
[[ -z "$backup" ]] || printf '旧软件备份：%s\n' "$backup"
if ! /usr/bin/open "$destination"; then
  printf '首次启动未完成，请在系统设置中查看应用安全提示；不要关闭系统安全保护。\n'
fi
/usr/bin/open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility' || true
printf '请亲自开启 CodexWhip 的辅助功能权限，并在蓝牙提示中点击允许。\n若授权列表里没有应用，请点击 +，按 Command+Shift+G，输入：%s\n' "$destination"
printf '授权后按需退出并重新打开已安装的软件。文件安装成功不等于授权或手柄功能已验收。\n'
