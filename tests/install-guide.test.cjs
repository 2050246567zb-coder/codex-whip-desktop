// Cross-platform metadata tests and sandboxed shell simulations; not a real Mac acceptance test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const scriptPath = path.join(root, 'docs/install-macos.sh');
const script = fs.readFileSync(scriptPath, 'utf8');
const jxa = script.match(/<<'JXA'\n([\s\S]*?)\nJXA/)[1];
const context = vm.createContext({});
vm.runInContext(jxa, context);
const prefix = 'https://github.com/2050246567zb-coder/codex-whip-desktop/releases/download/v2.2.90-macos/';
const packageName = 'CodexWhip-2.2.90-Apple-Silicon.dmg';
const sumsName = 'SHA256SUMS-2.2.90-macos.txt';
const mac = () => ({ tag_name: 'v2.2.90-macos', published_at: '2026-09-30T13:03:42Z',
  assets: [packageName, sumsName].map(name => ({ name, state: 'uploaded', size: 100,
    browser_download_url: prefix + name })) });
let checks = 0;
function check(name, action) { action(); checks++; console.log('PASS ' + name); }
const metadata = ['2.2.90', packageName, prefix + packageName, prefix + sumsName].join('\n');
check('latest Windows is not selected for Mac', () => assert.equal(context.selectRelease([
  {tag_name:'v2.2.94-windows', published_at:'2026-10-09T00:00:00Z'}, mac()]), metadata));
check('draft and prerelease Mac ignored', () => assert.equal(context.selectRelease([
  {...mac(), draft:true}, {...mac(), prerelease:true}, mac()]), metadata));
check('no Mac release requests next page', () => assert.equal(context.selectRelease([]), 'CONTINUE'));
check('malformed API result fails', () => assert.throws(() => context.selectRelease({message:'error'})));
check('missing checksum fails', () => { const r=mac(); r.assets.pop(); assert.throws(() => context.selectRelease([r])); });
check('foreign asset URL fails', () => { const r=mac(); r.assets[0].browser_download_url='https://example.com/app.dmg'; assert.throws(() => context.selectRelease([r])); });
check('duplicate asset fails', () => { const r=mac(); r.assets.push({...r.assets[0]}); assert.throws(() => context.selectRelease([r])); });
check('uploading asset fails', () => { const r=mac(); r.assets[0].state='uploading'; assert.throws(() => context.selectRelease([r])); });
check('Foundation read interface returns the same metadata', () => {
  context.ObjC={import(){},unwrap(value){return value;}};
  context.$={NSString:{stringWithContentsOfFileEncodingError(){return JSON.stringify([mac()]);}},NSUTF8StringEncoding:4};
  assert.equal(context.run(['fixture.json']), metadata);
});
const html = fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8');
check('copied prompt points to guide and requires actual installation', () => {
  const prompt = html.match(/id="prompt-text">([\s\S]*?)<\/p>/)[1];
  assert.ok(prompt.includes('/INSTALL.md'));
  assert.ok(prompt.includes('不要只下载或直接打开安装包'));
  assert.ok(prompt.includes('系统权限由我亲自确认'));
  assert.ok(html.includes('prompt.textContent.replace'));
});

const bash = process.env.CODEX_WHIP_TEST_BASH || (process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : '/bin/bash');
assert.equal(spawnSync(bash, ['-n', scriptPath], {encoding:'utf8'}).status, 0);
checks++;
// Commands operate only on the dedicated fixture home/temp. Mac APIs are mocks.
const mockShell = String.raw`
function /usr/bin/uname() { if [[ "$1" == -s ]]; then echo Darwin; else echo "\${SIM_ARCH:-arm64}"; fi; }
function /usr/sbin/sysctl() { echo "\${SIM_ARM_CAPABLE:-0}"; }
function /usr/bin/osascript() { /bin/cat "$SIM_METADATA"; }
function /usr/bin/curl() {
  local output='' url='' item
  while [[ $# -gt 0 ]]; do
    item="$1"; shift
    if [[ "$item" == --output ]]; then output="$1"; shift; else url="$item"; fi
  done
  printf '%s\n' "$url" >> "$SIM_LOG"
  case "$url" in
    *api.github.com*) printf '[]' > "$output" ;;
    *.dmg) printf 'test-dmg' > "$output" ;;
    *.txt)
      local hash
      hash=$(printf 'test-dmg' | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')
      if [[ "\${SIM_BAD_HASH:-0}" == 1 ]]; then hash=$(printf 'wrong' | /usr/bin/sha256sum | /usr/bin/awk '{print $1}'); fi
      printf '%s  %s\n' "$hash" 'CodexWhip-2.2.90-Apple-Silicon.dmg' > "$output" ;;
    *) return 1 ;;
  esac
}
function /usr/bin/shasum() { /usr/bin/sha256sum "\${@: -1}"; }
function /usr/bin/pgrep() { [[ "\${SIM_RUNNING:-0}" == 1 ]]; }
function /usr/bin/hdiutil() {
  if [[ "$1" == attach ]]; then
    local mount="\${@: -1}"
    /bin/mkdir -p "$mount/CodexWhip.app/Contents/MacOS"
    printf 'new-app' > "$mount/CodexWhip.app/Contents/MacOS/CodexWhip"
    printf 'fake-plist' > "$mount/CodexWhip.app/Contents/Info.plist"
  fi
}
function /usr/libexec/PlistBuddy() {
  if [[ "$2" == *Identifier* ]]; then echo "\${SIM_ID:-com.codexwhip.desktop}"; else echo 2.2.90; fi
}
function /usr/bin/codesign() {
  if [[ "\${SIM_BAD_SIGNATURE:-0}" == 1 ]]; then return 1; fi
  if [[ "\${SIM_BAD_STAGE_SIGNATURE:-0}" == 1 && "\${@: -1}" == *stage* ]]; then return 1; fi
  return 0
}
function /usr/bin/ditto() { /bin/cp -R "$1" "$2"; }
function /usr/bin/open() { printf 'OPEN %s\n' "$1" >> "$SIM_LOG"; }
function /bin/ln() { if [[ "\${SIM_LINK_FAIL:-0}" == 1 ]]; then return 1; fi; printf 'LINK %s\n' "$2" >> "$SIM_LOG"; }
function /bin/mv() {
  if [[ "\${SIM_MOVE_FAIL:-0}" == 1 && "$1" == *stage*/CodexWhip.app ]]; then return 1; fi
  command /bin/mv "$@"
}
source "$SIM_INSTALLER" "$@"
`.replaceAll(String.fromCharCode(92) + '${', '${');
const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-whip-installer-tests-'));
const shellPath = value => process.platform === 'win32' ? value.split(String.fromCharCode(92)).join('/').replace(/^([A-Z]):/i, (_, d) => '/'+d.toLowerCase()) : value;
let scenarioIndex = 0;
function simulate(flags={}, args=[], existing=false) {
  const dir = path.join(sandbox, 'case-'+(++scenarioIndex));
  fs.mkdirSync(dir);
  for (const sub of ['Applications','Desktop','tmp']) fs.mkdirSync(path.join(dir,sub));
  const target = path.join(dir,'Applications','CodexWhip.app');
  if (existing) {
    fs.mkdirSync(target);
    fs.writeFileSync(path.join(target,'old-marker'),'old-app');
  }
  fs.writeFileSync(path.join(dir,'settings.json'),'do-not-change');
  fs.writeFileSync(path.join(dir,'metadata'),metadata+'\n');
  const result=spawnSync(bash,['-c',mockShell,'mock-installer',...args],{encoding:'utf8',env:{...process.env,
    HOME:shellPath(dir),TMPDIR:shellPath(path.join(dir,'tmp')),
    SIM_METADATA:shellPath(path.join(dir,'metadata')),SIM_LOG:shellPath(path.join(dir,'commands.log')),
    SIM_INSTALLER:shellPath(scriptPath),...flags}});
  const log=fs.existsSync(path.join(dir,'commands.log')) ? fs.readFileSync(path.join(dir,'commands.log'),'utf8') : '';
  assert.equal(fs.readFileSync(path.join(dir,'settings.json'),'utf8'),'do-not-change');
  assert.equal(fs.readdirSync(path.join(dir,'tmp')).length,0,'temporary download directory leaked');
  return {result,dir,target,log};
}
// On the Windows test host /Applications is unavailable; the actual script falls back to fixture HOME/Applications.
if (process.platform === 'win32') {
  check('dry run does not install or launch', () => { const s=simulate({},['--dry-run']); assert.equal(s.result.status,0,s.result.stderr); assert.ok(!fs.existsSync(s.target)); assert.ok(!s.log.includes('.dmg')); });
  check('fresh install launches fixed path, requests shortcut and opens permission pane', () => { const s=simulate({},['--desktop-link']); assert.equal(s.result.status,0,s.result.stderr); assert.ok(fs.existsSync(path.join(s.target,'Contents/MacOS/CodexWhip'))); assert.ok(s.log.includes('OPEN '+shellPath(s.target))); assert.ok(s.log.includes('Privacy_Accessibility')); assert.ok(s.log.includes('LINK '+shellPath(s.target))); });
  check('update preserves old app backup and settings', () => { const s=simulate({},[],true); assert.equal(s.result.status,0,s.result.stderr); const base=path.join(s.dir,'Library/Application Support/CodexWhip/installer-backups'); const backups=fs.readdirSync(base); assert.equal(backups.length,1); assert.ok(fs.existsSync(path.join(base,backups[0],'old-marker'))); });
  check('denied desktop shortcut does not fail installation', () => { const s=simulate({SIM_LINK_FAIL:'1'},['--desktop-link']); assert.equal(s.result.status,0,s.result.stderr); assert.ok(fs.existsSync(path.join(s.target,'Contents/MacOS/CodexWhip'))); assert.ok(s.log.includes('OPEN '+shellPath(s.target))); });
  for (const [name, flags] of [
    ['bad checksum',{SIM_BAD_HASH:'1'}],['bad signature',{SIM_BAD_SIGNATURE:'1'}],
    ['bad staged signature',{SIM_BAD_STAGE_SIGNATURE:'1'}],['wrong app identity',{SIM_ID:'wrong.app'}],
    ['running app',{SIM_RUNNING:'1'}],['failed replacement rollback',{SIM_MOVE_FAIL:'1'}]
  ]) check(name+' leaves old installation intact',()=>{const s=simulate(flags,[],true);assert.notEqual(s.result.status,0);assert.ok(fs.existsSync(path.join(s.target,'old-marker')));assert.ok(!s.log.includes('OPEN '));});
  check('Intel Mac rejected before network or installation',()=>{const s=simulate({SIM_ARCH:'x86_64',SIM_ARM_CAPABLE:'0'});assert.notEqual(s.result.status,0);assert.equal(s.log,'');assert.ok(!fs.existsSync(s.target));});
  check('Rosetta Apple Silicon detected',()=>{const s=simulate({SIM_ARCH:'x86_64',SIM_ARM_CAPABLE:'1'},['--dry-run']);assert.equal(s.result.status,0,s.result.stderr);});
}
console.log(JSON.stringify({checks,real_mac_tested:false,fixture_directory:sandbox}));
