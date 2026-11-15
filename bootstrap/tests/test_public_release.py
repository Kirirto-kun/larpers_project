"""Public release builder: boundaries and real starter execution."""
from __future__ import annotations
import gzip, hashlib, importlib.util, io, json, os
from pathlib import Path
import shutil, struct, subprocess, sys, tarfile
import pytest
ROOT = Path(__file__).resolve().parents[2]
# Public X.509 certificate fixture; no private key or host credentials.
TEST_CA = b'-----BEGIN CERTIFICATE-----\nMIIBozCCAUmgAwIBAgIQCTO6VM6u1zSqBRHYBgtf0TAKBggqhkjOPQQDAjAwMS4w\nLAYDVQQDEyVDYWRkeSBMb2NhbCBBdXRob3JpdHkgLSAyMDI2IEVDQyBSb290MB4X\nDTI2MTExNDEwMTk1MVoXDTM2MDkyMjEwMTk1MVowMDEuMCwGA1UEAxMlQ2FkZHkg\nTG9jYWwgQXV0aG9yaXR5IC0gMjAyNiBFQ0MgUm9vdDBZMBMGByqGSM49AgEGCCqG\nSM49AwEHA0IABGWznLmtGYvO0ZFgWj40f63c0n+XQmcxU0CgVAhPPgRLgsw9/tIz\npcpi5AzLQ4wWiJSgtHl1oJBQ530gg84PpwqjRTBDMA4GA1UdDwEB/wQEAwIBBjAS\nBgNVHRMBAf8ECDAGAQH/AgEBMB0GA1UdDgQWBBQdxf7pKsTJWZCrlsrcZ/F5AAiW\nkDAKBggqhkjOPQQDAgNIADBFAiAXayJ/F/M0lE3XNAt5wCpjKlhQ6YNXA0BoSAyi\nFrpqzgIhANEgF4bwyfYYZefNuI0U49Ib+zTykAYY+6CkoGaKYl2z\n-----END CERTIFICATE-----\n'


def builder():
    source = ROOT / 'bootstrap/prepare_public_release.py'
    assert source.exists(), 'public release builder must exist'
    spec = importlib.util.spec_from_file_location('public_release_builder', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(value): return hashlib.sha256(value).hexdigest()


def native(goos, arch, *, cgo='0'):
    if goos == 'darwin':
        header = struct.pack('<8I', 0xFEEDFACF, 0x0100000C if arch=='arm64' else 0x01000007, 0, 2, 0, 0, 0, 0)
    elif goos == 'windows':
        header = bytearray(256); header[:2] = b'MZ'; struct.pack_into('<I', header,60,128)
        header[128:132]=b'PE\0\0'; struct.pack_into('<H',header,132,0xAA64 if arch=='arm64' else 0x8664)
        struct.pack_into('<H',header,150,0x22); struct.pack_into('<H',header,152,0x20B)
    else:
        header = bytearray(64); header[:6]=b'\x7fELF\x02\x01'; struct.pack_into('<H',header,16,2)
        struct.pack_into('<H',header,18,183 if arch=='arm64' else 62)
    info = ('path\tcampus/runner/cmd/campus\nbuild\tGOOS='+goos+'\nbuild\tGOARCH='+arch+'\nbuild\tCGO_ENABLED='+cgo+'\n').encode()
    module=b'x'*16+info+b'y'*16
    def varint(n):
        out=bytearray()
        while n>=128: out.append((n&127)|128); n>>=7
        return bytes(out+bytes([n]))
    return bytes(header)+b'\0'*((-len(header))%16)+b'\xff Go buildinf:'+bytes([8,2])+b'\0'*16+b'\x08go1.25.6'+varint(len(module))+module


def archive(arch, kind, *, wrong_guard=False, unsafe=False):
    config=json.dumps({'architecture':arch,'os':'linux','config':{'Labels':{'io.campus.guard':'0' if wrong_guard else '1','io.campus.accelerator':kind}}}).encode()
    manifest=json.dumps({'schemaVersion':2,'config':{'digest':'sha256:'+sha(config)},'layers':[]}).encode()
    identity='sha256:'+sha(manifest)
    index=json.dumps({'schemaVersion':2,'manifests':[{'digest':identity}]}).encode()
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as tar:
        for name,data in [('index.json',index),('blobs/sha256/'+sha(manifest),manifest),('blobs/sha256/'+sha(config),config)]:
            entry=tarfile.TarInfo('../escape' if unsafe and name=='index.json' else name); entry.size=len(data)
            tar.addfile(entry,io.BytesIO(data))
    return identity,gzip.compress(stream.getvalue(),mtime=0)


@pytest.fixture
def inputs(tmp_path):
    binaries=tmp_path/'fresh binaries'; binaries.mkdir()
    for goos in ('darwin','windows','linux'):
        for arch in ('amd64','arm64'):
            (binaries/('campus-'+goos+'-'+arch+('.exe' if goos=='windows' else ''))).write_bytes(native(goos,arch))
    profiles=[]; archives={}
    for arch,kind in [('amd64','cpu'),('arm64','cpu'),('amd64','cuda')]:
        identity,data=archive(arch,kind); path=tmp_path/(kind+'-'+arch+'.tar.gz'); path.write_bytes(data); archives[kind+'_'+arch+'_archive']=path
        profiles.append({'id':kind+'-'+arch,'platform':'linux/'+arch,'accelerator':kind,'image_digest':identity,'guard_version':'1','invitation':'never-public-token','dockerfile':'must-not-ship'})
    profile_path=tmp_path/'profiles.json'; profile_path.write_text(json.dumps({'schema_version':'1','profiles':profiles}))
    return dict(binaries=binaries,profiles=profile_path,out=tmp_path/'public \u7a7a\u95f4 & !',endpoint='https://pool.example:8443',base_url='https://pool.example:8443/downloads/v1',**archives)


def test_public_catalog_uses_shared_verified_archives_and_strips_private_fields(inputs):
    out=builder().prepare_release(**inputs)
    catalog=json.loads((out/'catalog.json').read_text(encoding='utf-8'))
    assert set(catalog)=={'schema_version','endpoint','targets'}
    assert catalog['schema_version']=='1' and catalog['endpoint']=='https://pool.example:8443'
    assert len(catalog['targets'])==6
    for target in catalog['targets']:
        kinds={image['accelerator'] for image in target['images']}
        assert kinds==({'cpu','cuda'} if target['arch']=='amd64' and target['os']!='darwin' else {'cpu'})
        for ref in [target['runner'],target['profiles'],*target['images']]:
            payload=(out/ref['path']).read_bytes()
            assert sha(payload)==ref['sha256'] and len(payload)==ref['size_bytes']
        prepared=json.loads((out/target['profiles']['path']).read_text(encoding='utf-8'))
        assert all(set(p)=={'id','platform','accelerator','image_digest','guard_version'} for p in prepared['profiles'])
    assert len(list((out/'images').iterdir()))==3
    assert os.path.samefile(out/'images/cpu-amd64.tar.gz',inputs['cpu_amd64_archive']), 'large verified assets must be hardlinked rather than recompressed/copied'
    assert {p.name for p in out.iterdir()} >= {'Connect.cmd','Connect.command','catalog.json','SHA256SUMS'}
    public_text='\n'.join(p.read_text(encoding='utf-8') for p in out.rglob('*') if p.is_file() and p.suffix not in {'.gz','.exe'} and 'runners' not in p.parts)
    assert 'never-public-token' not in public_text and 'must-not-ship' not in public_text
    assert not list(out.rglob('invite*')) and not list(out.rglob('*.tar'))


@pytest.mark.parametrize('goos,arch',[('darwin','amd64'),('darwin','arm64'),('windows','amd64'),('windows','arm64'),('linux','amd64'),('linux','arm64')])
def test_wrong_native_arch_or_cgo_never_publishes(inputs,goos,arch):
    path=inputs['binaries']/('campus-'+goos+'-'+arch+('.exe' if goos=='windows' else ''))
    path.write_bytes(native(goos,arch,cgo='1'))
    with pytest.raises(ValueError): builder().prepare_release(**inputs)
    assert not inputs['out'].exists()


@pytest.mark.parametrize('url',['http://pool.example/downloads/v1','https://user:secret@pool.example/v1','https://pool.example/v1?token=x','https://pool.example/v1/../x','https://pool.example/v1/quote\'x','https://pool.example/v1/%2fescape'])
def test_unsafe_public_base_url_rejected(inputs,url):
    inputs['base_url']=url
    with pytest.raises(ValueError): builder().prepare_release(**inputs)
    assert not inputs['out'].exists()


@pytest.mark.parametrize('change',['wrong_guard','wrong_platform','wrong_accelerator','unsafe','tamper','wrong_identity'])
def test_archive_identity_guard_and_tar_safety_fail_closed(inputs,change):
    if change in {'wrong_guard','unsafe','wrong_platform','wrong_accelerator'}:
        identity,data=archive('arm64' if change=='wrong_platform' else 'amd64','cuda' if change=='wrong_accelerator' else 'cpu',**({change:True} if change in {'wrong_guard','unsafe'} else {}))
        if change!='unsafe':
            profiles=json.loads(inputs['profiles'].read_text(encoding='utf-8')); profiles['profiles'][0]['image_digest']=identity
            inputs['profiles'].write_text(json.dumps(profiles))
    elif change=='tamper': data=inputs['cpu_amd64_archive'].read_bytes()[:-10]
    else: _,data=archive('arm64','cpu')
    inputs['cpu_amd64_archive'].write_bytes(data)
    with pytest.raises(ValueError): builder().prepare_release(**inputs)
    assert not inputs['out'].exists()


def test_subset_binaries_no_cuda_archive_and_no_overwrite(inputs):
    for path in inputs['binaries'].iterdir():
        if path.name!='campus-darwin-arm64': path.unlink()
    inputs.pop('cuda_amd64_archive')
    out=builder().prepare_release(**inputs)
    catalog=json.loads((out/'catalog.json').read_text(encoding='utf-8'))
    assert len(catalog['targets'])==1 and catalog['targets'][0]['os']=='darwin'
    assert len(list((out/'images').iterdir()))==1
    with pytest.raises(FileExistsError): builder().prepare_release(**inputs)

def posix(path):
    value=str(path).replace('\\','/')
    return '/'+value[0].lower()+value[2:] if len(value)>1 and value[1]==':' else value


def test_posix_starter_selects_actual_rosetta_arm_reuses_cache_and_preserves_exit(tmp_path):
    shell=Path('C:/Program Files/Git/bin/sh.exe') if os.name=='nt' else Path(shutil.which('sh') or 'missing')
    if not shell.exists(): pytest.skip('POSIX shell unavailable')
    module=builder(); root=tmp_path/'public \u7a7a\u95f4 & !'; root.mkdir(); (root/'catalog.json').write_text('{}')
    source=tmp_path/'downloaded runner'; source.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$PUBLIC_ARGS"\nexit "${PUBLIC_EXIT:-0}"\n',newline='\n')
    pins={'schema_version':'1','endpoint':'https://pool.example','targets':[{'os':'darwin','arch':'arm64','runner':{'path':'runners/arm64','sha256':sha(source.read_bytes()),'size_bytes':source.stat().st_size}}]}
    module.write_starters(root,pins,'https://pool.example/downloads/v1')
    boundary=tmp_path/'boundary'; boundary.mkdir(); home=tmp_path/'home \u7a7a\u95f4'; home.mkdir()
    (boundary/'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo x86_64;; esac\n',newline='\n')
    (boundary/'sysctl').write_text('#!/bin/sh\necho 1\n',newline='\n')
    (boundary/'curl').write_text('#!/bin/sh\nprintf "%s\\0" "$@" >> "$PUBLIC_CURL_ARGS"\nwhile [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then shift; out=$1; fi; shift; done\ncp "$PUBLIC_PAYLOAD" "$out"\nprintf "%s" "${PUBLIC_HTTP_STATUS:-200}"\n',newline='\n')
    for path in boundary.iterdir(): path.chmod(0o700)
    marker=tmp_path/'arguments.bin'; trace=tmp_path/'curl.bin'
    env={**os.environ,'HOME':posix(home),'PUBLIC_ARGS':posix(marker),'PUBLIC_CURL_ARGS':posix(trace),'PUBLIC_PAYLOAD':posix(source),'PUBLIC_EXIT':'7',
         'PATH':posix(boundary)+':/usr/bin/core_perl:/usr/bin:/bin:'+os.environ.get('PATH','')}
    env['PUBLIC_TEST_PATH']=posix(boundary)+':/usr/bin/core_perl:/usr/bin:/bin'
    command=[str(shell),'-c','PATH="$PUBLIC_TEST_PATH"; export PATH; exec sh "$1"','public-starter',posix(root/'Connect.command')]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==7,result.stdout+result.stderr
    assert marker.read_bytes().decode().split('\0')[:-1]==['install','--catalog-url','https://pool.example/downloads/v1/catalog.json','--catalog-sha256',sha(b'{}'),'--managed']
    args=trace.read_bytes().decode().split('\0')[:-1]
    assert args[-1]=='https://pool.example/downloads/v1/runners/arm64'
    assert '--insecure' not in args and '--location' not in args
    before=trace.read_bytes(); env['PUBLIC_EXIT']='130'
    assert subprocess.run(command,env=env,capture_output=True,timeout=30).returncode==130
    assert trace.read_bytes()==before,'verified native client must be reused'
    cached=next((home/'Library/Caches/Campus/starter').glob('runner-*'))
    cached.write_bytes(b'tampered'); marker.unlink(); env['PUBLIC_HTTP_STATUS']='301'
    assert subprocess.run(command,env=env,capture_output=True,timeout=30).returncode!=0
    assert not marker.exists(),'redirect responses must never execute'
    env['PUBLIC_HTTP_STATUS']='200'; source.write_bytes(b'bad download')
    assert subprocess.run(command,env=env,capture_output=True,timeout=30).returncode!=0
    assert not marker.exists(),'unverified download must never execute'
    assert not list((home/'Library/Caches/Campus/starter').glob('.download.*'))


@pytest.fixture(scope='session')
def native_boundary(tmp_path_factory):
    if os.name!='nt': pytest.skip('Windows native boundary requires Windows')
    folder=tmp_path_factory.mktemp('public-go-boundary'); source=folder/'main.go'; binary=folder/'boundary.exe'
    source.write_text('''package main
import("os";"os/exec";"os/user";"syscall";"unsafe";"encoding/json";"io";"strconv")
func main(){
 if len(os.Args)>1 && (os.Args[1]=="install" || os.Args[1]=="--ca-file") {if path:=os.Getenv("PUBLIC_RESTRICT_CACHE");path!=""{if err:=restrict(path);err!=nil{os.Stderr.WriteString(err.Error());os.Exit(19)}};if probe:=os.Getenv("PUBLIC_MODULE_PROBE");probe!=""{cmd:=exec.Command("powershell.exe","-NoLogo","-NoProfile","-NonInteractive","-Command","$ErrorActionPreference='Stop'; Import-Module Microsoft.PowerShell.Security; Import-Module CimCmdlets; Get-CimInstance Win32_OperatingSystem -ErrorAction Stop | Out-Null");out,err:=cmd.CombinedOutput();ok:=err==nil;b,_:=json.Marshal(map[string]any{"module_path":os.Getenv("PSModulePath"),"ok":ok,"output":string(out)});os.WriteFile(probe,b,0600);if !ok{os.Exit(17)}};b,_:=json.Marshal(os.Args[1:]);os.WriteFile(os.Getenv("PUBLIC_ARGS"),b,0600); n,_:=strconv.Atoi(os.Getenv("PUBLIC_EXIT"));os.Exit(n)}
 b,_:=json.Marshal(os.Args[1:]); f,_:=os.OpenFile(os.Getenv("PUBLIC_CURL_ARGS"),os.O_CREATE|os.O_WRONLY|os.O_APPEND,0600);f.Write(append(b,'\\n'));f.Close()
 out:="";for i,arg:=range os.Args {if arg=="--output" && i+1<len(os.Args){out=os.Args[i+1]}}
 in,e:=os.Open(os.Getenv("PUBLIC_PAYLOAD"));if e!=nil{os.Exit(9)};defer in.Close();w,e:=os.Create(out);if e!=nil{os.Exit(8)};io.Copy(w,in);w.Close();if code:=os.Getenv("PUBLIC_CURL_EXIT");code!=""{n,_:=strconv.Atoi(code);os.Exit(n)};os.Stdout.WriteString("200")
}
func restrict(name string)error{u,err:=user.Current();if err!=nil{return err};sddl,_:=syscall.UTF16PtrFromString("D:P(A;OICI;FA;;;"+u.Uid+")");path,_:=syscall.UTF16PtrFromString(name);advapi:=syscall.NewLazyDLL("advapi32.dll");var descriptor uintptr;r,_,e:=advapi.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW").Call(uintptr(unsafe.Pointer(sddl)),1,uintptr(unsafe.Pointer(&descriptor)),0);if r==0{return e};defer syscall.LocalFree(syscall.Handle(descriptor));r,_,e=advapi.NewProc("SetFileSecurityW").Call(uintptr(unsafe.Pointer(path)),0x80000004,descriptor);if r==0{return e};return nil}
''')
    go=Path('C:/Users/jafar/.cache/campus-tools/go/bin/go.exe')
    subprocess.run([str(go),'build','-o',str(binary),str(source)],check=True,capture_output=True,timeout=120)
    return binary


@pytest.mark.skipif(os.name!='nt',reason='native Windows starter requires Windows')
@pytest.mark.parametrize('entry',['Connect.cmd','Connect.ps1'])
@pytest.mark.parametrize('exit_code',[0,7,130])
def test_windows_starter_native_cache_checksums_unicode_path_and_exit(tmp_path,native_boundary,entry,exit_code):
    root=tmp_path/'public \u7a7a\u95f4 & !'; root.mkdir(); (root/'catalog.json').write_text('{}')
    binary=native_boundary.read_bytes(); pins={'targets':[{'os':'windows','arch':'amd64','runner':{'path':'runners/campus-windows-amd64.exe','sha256':sha(binary),'size_bytes':len(binary)}}]}
    builder().write_starters(root,pins,'https://pool.example/downloads/v1')
    boundary=tmp_path/'curl boundary'; boundary.mkdir(); shutil.copyfile(native_boundary,boundary/'curl.exe')
    marker=tmp_path/'native args.json'; trace=tmp_path/'curl.jsonl'; cache=tmp_path/'private cache \u7a7a\u95f4 & !'; cache.mkdir()
    env={**os.environ,'LOCALAPPDATA':str(cache),'PUBLIC_ARGS':str(marker),'PUBLIC_CURL_ARGS':str(trace),'PUBLIC_PAYLOAD':str(native_boundary),'PUBLIC_EXIT':str(exit_code),
         'PATH':str(boundary)+os.pathsep+os.environ.get('PATH','')}
    command='cmd.exe /d /s /c ""'+str(root/entry)+'""' if entry.endswith('.cmd') else ['powershell.exe','-NoLogo','-NoProfile','-File',str(root/entry)]
    result=subprocess.run(command,env=env,cwd=tmp_path,input='',capture_output=True,text=True,errors='replace',timeout=30)
    assert result.returncode==exit_code,result.stdout+result.stderr
    assert ('Press Enter' in result.stdout)==(entry.endswith('.cmd') and exit_code==7)
    assert json.loads(marker.read_text(encoding='utf-8'))==['install','--catalog-url','https://pool.example/downloads/v1/catalog.json','--catalog-sha256',sha(b'{}'),'--managed','--gpu=false']
    curl_args=json.loads(trace.read_text(encoding='utf-8').splitlines()[0]); assert curl_args[-1]=='https://pool.example/downloads/v1/runners/campus-windows-amd64.exe'
    assert '--insecure' not in curl_args and '--ssl-revoke-best-effort' not in curl_args
    old=trace.read_bytes(); subprocess.run(command,env=env,cwd=tmp_path,input='',capture_output=True,text=True,timeout=30)
    assert trace.read_bytes()==old
    cache_runner=next((cache/'Campus/starter').glob('runner-*.exe')); cache_runner.write_bytes(b'tampered')
    marker.unlink(); env['PUBLIC_PAYLOAD']=str(root/'catalog.json')
    failed=subprocess.run(command,env=env,cwd=tmp_path,input='',capture_output=True,text=True,timeout=30)
    assert failed.returncode!=0 and not marker.exists()
    assert len(failed.stderr)<1024,'download errors must not dump the embedded PowerShell program'

def test_public_ca_is_pinned_certificate_only_and_private_keys_never_publish(inputs,tmp_path):
    ca=tmp_path/'ca.pem'; payload=TEST_CA; ca.write_bytes(payload)
    out=builder().prepare_release(**inputs,ca_file=ca)
    catalog=json.loads((out/'catalog.json').read_text(encoding='utf-8'))
    assert catalog['ca_file']=={'path':'ca.pem','sha256':sha(payload),'size_bytes':len(payload)}
    assert (out/'ca.pem').read_bytes()==payload
    ca.write_bytes(payload+b'-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n')
    inputs['out']=tmp_path/'rejected-release'
    with pytest.raises(ValueError): builder().prepare_release(**inputs,ca_file=ca)
    assert not inputs['out'].exists()


@pytest.mark.skipif(os.name!='nt',reason='native Windows starter requires Windows')
def test_windows_custom_ca_is_private_and_only_it_enables_best_effort_revocation(tmp_path,native_boundary):
    root=tmp_path/'public \u7a7a\u95f4 & !'; root.mkdir(); (root/'catalog.json').write_text('{}')
    binary=native_boundary.read_bytes(); pins={'targets':[{'os':'windows','arch':'arm64','runner':{'path':'runners/campus-windows-arm64.exe','sha256':sha(binary),'size_bytes':len(binary)}}]}
    ca=TEST_CA
    builder().write_starters(root,pins,'https://pool.example/downloads/v1',ca_payload=ca)
    boundary=tmp_path/'curl boundary'; boundary.mkdir(); shutil.copyfile(native_boundary,boundary/'curl.exe')
    marker=tmp_path/'args.json'; trace=tmp_path/'curl.jsonl'; cache=tmp_path/'cache'; cache.mkdir()
    env={**os.environ,'LOCALAPPDATA':str(cache),'PUBLIC_ARGS':str(marker),'PUBLIC_CURL_ARGS':str(trace),'PUBLIC_PAYLOAD':str(native_boundary),'PUBLIC_EXIT':'0',
         'PROCESSOR_ARCHITECTURE':'AMD64','PROCESSOR_ARCHITEW6432':'ARM64','PATH':str(boundary)+os.pathsep+os.environ.get('PATH','')}
    command='cmd.exe /d /s /c ""'+str(root/'Connect.cmd')+'""'
    result=subprocess.run(command,env=env,cwd=tmp_path,input='',capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    args=json.loads(marker.read_text(encoding='utf-8')); ca_path=cache/'Campus/starter'/('ca-'+sha(ca)+'.pem')
    assert args[:3]==['--ca-file',str(ca_path),'install'] and ca_path.read_bytes()==ca
    curl_args=json.loads(trace.read_text(encoding='utf-8').splitlines()[0])
    assert '--ssl-revoke-best-effort' in curl_args and curl_args[curl_args.index('--cacert')+1]==str(ca_path)
    assert curl_args[-1]=='https://pool.example/downloads/v1/runners/campus-windows-arm64.exe'
    acl=subprocess.run(['icacls',str(cache/'Campus/starter')],capture_output=True,text=True,check=True)
    assert '(I)' not in acl.stdout


def test_archive_rejects_excess_small_metadata_memory(inputs):
    identity,payload=archive('amd64','cpu')
    source=io.BytesIO(gzip.decompress(payload)); destination=io.BytesIO()
    with tarfile.open(fileobj=source,mode='r') as original, tarfile.open(fileobj=destination,mode='w') as output:
        for entry in original: output.addfile(entry,original.extractfile(entry))
        for number in range(9):
            value=json.dumps({'extra':str(number)+'x'*(1<<20)}).encode(); entry=tarfile.TarInfo('blobs/sha256/'+sha(value)); entry.size=len(value)
            output.addfile(entry,io.BytesIO(value))
    inputs['cpu_amd64_archive'].write_bytes(gzip.compress(destination.getvalue(),mtime=0))
    with pytest.raises(ValueError,match='metadata'): builder().prepare_release(**inputs)
    assert not inputs['out'].exists()


@pytest.mark.parametrize('payload',[b'-----BEGIN CERTIFICATE-----\nAQID\n-----END CERTIFICATE-----\n',b'-----BEGIN CERTIFICATE-----\ninvalid-base64!\n-----END CERTIFICATE-----\n'])
def test_invalid_x509_ca_does_not_publish(inputs,tmp_path,payload):
    ca=tmp_path/'bad-ca.pem';ca.write_bytes(payload)
    with pytest.raises(ValueError): builder().prepare_release(**inputs,ca_file=ca)
    assert not inputs['out'].exists()

def test_oci_archive_cannot_omit_a_referenced_layer(inputs):
    config=json.dumps({'architecture':'amd64','os':'linux','config':{'Labels':{'io.campus.guard':'1','io.campus.accelerator':'cpu'}}}).encode()
    manifest=json.dumps({'schemaVersion':2,'config':{'digest':'sha256:'+sha(config)},'layers':[{'digest':'sha256:'+'0'*64}]}).encode()
    identity='sha256:'+sha(manifest); index=json.dumps({'schemaVersion':2,'manifests':[{'digest':identity}]}).encode()
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as tar:
        for name,data in [('index.json',index),('blobs/sha256/'+sha(manifest),manifest),('blobs/sha256/'+sha(config),config)]:
            entry=tarfile.TarInfo(name);entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
    inputs['cpu_amd64_archive'].write_bytes(gzip.compress(stream.getvalue(),mtime=0))
    profiles=json.loads(inputs['profiles'].read_text(encoding='utf-8'));profiles['profiles'][0]['image_digest']=identity
    inputs['profiles'].write_text(json.dumps(profiles))
    with pytest.raises(ValueError,match='referenced'): builder().prepare_release(**inputs)
    assert not inputs['out'].exists()


@pytest.mark.skipif(os.name!='nt',reason='native Windows starter requires Windows')
@pytest.mark.parametrize('cancel_code',['130','-1073741510'])
def test_windows_download_ctrl_c_exits_130_without_error_pause(tmp_path,native_boundary,cancel_code):
    root=tmp_path/'public \u7a7a\u95f4 & !';root.mkdir();(root/'catalog.json').write_text('{}')
    binary=native_boundary.read_bytes();pins={'targets':[{'os':'windows','arch':'amd64','runner':{'path':'runners/client.exe','sha256':sha(binary),'size_bytes':len(binary)}}]}
    builder().write_starters(root,pins,'https://pool.example/downloads/v1')
    boundary=tmp_path/'boundary';boundary.mkdir();shutil.copyfile(native_boundary,boundary/'curl.exe')
    cache=tmp_path/'cache';cache.mkdir();marker=tmp_path/'must-not-run.json'
    env={**os.environ,'LOCALAPPDATA':str(cache),'PUBLIC_PAYLOAD':str(native_boundary),'PUBLIC_ARGS':str(marker),'PUBLIC_CURL_ARGS':str(tmp_path/'curl.jsonl'),
         'PUBLIC_CURL_EXIT':cancel_code,'PATH':str(boundary)+os.pathsep+os.environ.get('PATH','')}
    result=subprocess.run('cmd.exe /d /s /c ""'+str(root/'Connect.cmd')+'""',env=env,input='',capture_output=True,text=True,errors='replace',timeout=30)
    assert result.returncode==130,result.stdout+result.stderr
    assert 'Press Enter' not in result.stdout and not marker.exists()
    assert not list((cache/'Campus/starter').glob('.download-*'))

@pytest.mark.parametrize('change',['missing','wrong_bytes','wrong_order','wrong_count','directory','unsafe','compressed','bad_diff_ids','bad_layers','valid'])
def test_legacy_archive_enforces_ordered_raw_layers_bound_by_config(inputs,tmp_path,change):
    first=io.BytesIO();second=io.BytesIO()
    with tarfile.open(fileobj=first,mode='w') as tar: pass
    with tarfile.open(fileobj=second,mode='w') as tar:
        value=b'layer two';entry=tarfile.TarInfo('data.txt');entry.size=len(value);tar.addfile(entry,io.BytesIO(value))
    layers=[first.getvalue(),second.getvalue()]
    def prepare(change):
        blobs=list(layers);names=['first/layer.tar','second/layer.tar'];references=list(names);diff_ids=['sha256:'+sha(v) for v in blobs]
        if change=='missing': blobs=blobs[:1]
        if change=='wrong_bytes': blobs[1]=blobs[0]
        if change=='wrong_order': references.reverse()
        if change=='wrong_count': diff_ids=diff_ids[:1]
        if change=='directory': blobs=blobs[:1]
        if change=='unsafe': references[1]='../second/layer.tar'
        if change=='compressed':
            blobs[1]=gzip.compress(blobs[1],mtime=0);diff_ids[1]='sha256:'+sha(blobs[1])
        if change=='bad_diff_ids': diff_ids=['not-a-digest','not-a-digest']
        if change=='bad_layers': references='first/layer.tar'
        config=json.dumps({'architecture':'amd64','os':'linux','config':{'Labels':{'io.campus.guard':'1','io.campus.accelerator':'cpu'}},'rootfs':{'type':'layers','diff_ids':diff_ids}}).encode()
        identity='sha256:'+sha(config);manifest=json.dumps([{'Config':identity[7:]+'.json','RepoTags':['campus/cpu:approved'],'Layers':references}]).encode()
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w') as tar:
            for name,value in [(identity[7:]+'.json',config),('manifest.json',manifest),*zip(names,blobs)]:
                entry=tarfile.TarInfo(name);entry.size=len(value);tar.addfile(entry,io.BytesIO(value))
            if change=='directory':
                entry=tarfile.TarInfo(names[1]);entry.type=tarfile.DIRTYPE;tar.addfile(entry)
        inputs['cpu_amd64_archive'].write_bytes(gzip.compress(stream.getvalue(),mtime=0))
        profiles=json.loads(inputs['profiles'].read_text(encoding='utf-8'));profiles['profiles'][0]['image_digest']=identity
        inputs['profiles'].write_text(json.dumps(profiles));inputs['out']=tmp_path/('legacy-'+change)
        return builder().prepare_release(**inputs)
    if change=='valid':
        out=prepare(change)
        assert (out/'catalog.json').is_file(),'valid ordered raw Docker-save layers remain supported'
    else:
        with pytest.raises(ValueError): prepare(change)
        assert not inputs['out'].exists(),change+' must never publish'


@pytest.mark.skipif(os.name!='nt',reason='native Windows starter requires Windows')
@pytest.mark.parametrize('host,exit_code',[('cmd',0),('powershell',0),('pwsh',0),('pwsh',1),('pwsh',7),('pwsh',130)])
def test_windows_starter_inherits_ps7_module_path_and_child_cim_works(tmp_path,native_boundary,host,exit_code):
    inherited=os.environ.get('PSModulePath','')
    candidates=[Path(value).parent/'pwsh.exe' for value in inherited.split(os.pathsep) if value]
    pwsh=next((path for path in candidates if path.is_file()),None)
    if pwsh is None: pytest.skip('an inherited PowerShell7 module path is unavailable')
    root=tmp_path/'public \u7a7a\u95f4 & !';root.mkdir();(root/'catalog.json').write_text('{}')
    binary=native_boundary.read_bytes();pins={'targets':[{'os':'windows','arch':'amd64','runner':{'path':'runners/client.exe','sha256':sha(binary),'size_bytes':len(binary)}}]}
    builder().write_starters(root,pins,'https://pool.example/downloads/v2')
    boundary=tmp_path/'boundary';boundary.mkdir();shutil.copyfile(native_boundary,boundary/'curl.exe')
    cache=tmp_path/'cache';cache.mkdir();marker=tmp_path/'args.json';probe=tmp_path/'module-probe.json'
    env={**os.environ,'LOCALAPPDATA':str(cache),'PUBLIC_PAYLOAD':str(native_boundary),'PUBLIC_ARGS':str(marker),'PUBLIC_CURL_ARGS':str(tmp_path/'curl.jsonl'),
         'PUBLIC_MODULE_PROBE':str(probe),'PUBLIC_EXIT':str(exit_code),'PATH':str(boundary)+os.pathsep+os.environ.get('PATH','')}
    command='cmd.exe /d /s /c ""'+str(root/'Connect.cmd')+'""' if host=='cmd' else [str(pwsh) if host=='pwsh' else 'powershell.exe','-NoLogo','-NoProfile','-File',str(root/'Connect.ps1')]
    parent_probe=tmp_path/'parent-module-path.txt'
    if host=='pwsh':
        env['PUBLIC_STARTER_FILE']=str(root/'Connect.ps1');env['PUBLIC_PARENT_MODULE_PATH']=str(parent_probe)
        wrapper="$code=1; try { & ([Environment]::GetEnvironmentVariable('PUBLIC_STARTER_FILE','Process')); $code=$LASTEXITCODE } finally { [IO.File]::WriteAllText([Environment]::GetEnvironmentVariable('PUBLIC_PARENT_MODULE_PATH','Process'),[Environment]::GetEnvironmentVariable('PSModulePath','Process')) }; exit $code"
        command=[str(pwsh),'-NoLogo','-NoProfile','-Command',wrapper]
    if exit_code==1: env['PUBLIC_PAYLOAD']=str(root/'catalog.json')
    result=subprocess.run(command,env=env,input='',capture_output=True,text=True,errors='replace',timeout=45)
    assert result.returncode==exit_code,result.stdout+result.stderr
    if host=='pwsh': assert parent_probe.read_text(encoding='utf-8')==inherited
    if exit_code==1:
        assert not marker.exists()
        return
    assert marker.is_file()
    evidence=json.loads(probe.read_text(encoding='utf-8'))
    assert evidence['ok'],evidence['output']
    assert evidence['module_path'].casefold()==str(Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/Modules').casefold()


@pytest.mark.skipif(os.name!='nt',reason='native Windows starter requires Windows')
@pytest.mark.parametrize('entry',['Connect.cmd','Connect.ps1'])
def test_windows_repeat_cache_after_native_dacl_protection_needs_no_security_privilege(tmp_path,native_boundary,entry):
    privileges=subprocess.run(['whoami','/priv'],capture_output=True,text=True,check=True).stdout
    assert 'SeSecurityPrivilege' not in privileges,'regression must run without audit/security privilege'
    root=tmp_path/'public \u7a7a\u95f4 & !';root.mkdir();(root/'catalog.json').write_text('{}')
    binary=native_boundary.read_bytes();pins={'targets':[{'os':'windows','arch':'amd64','runner':{'path':'runners/client.exe','sha256':sha(binary),'size_bytes':len(binary)}}]}
    builder().write_starters(root,pins,'https://pool.example/downloads/v3')
    boundary=tmp_path/'boundary';boundary.mkdir();shutil.copyfile(native_boundary,boundary/'curl.exe')
    cache=tmp_path/'cache';cache.mkdir();marker=tmp_path/'args.json';trace=tmp_path/'curl.jsonl'
    protected=cache/'Campus/starter'
    env={**os.environ,'LOCALAPPDATA':str(cache),'PUBLIC_PAYLOAD':str(native_boundary),'PUBLIC_ARGS':str(marker),'PUBLIC_CURL_ARGS':str(trace),'PUBLIC_EXIT':'0',
         'PUBLIC_RESTRICT_CACHE':str(protected),'PATH':str(boundary)+os.pathsep+os.environ.get('PATH','')}
    command='cmd.exe /d /s /c ""'+str(root/entry)+'""' if entry.endswith('.cmd') else ['powershell.exe','-NoLogo','-NoProfile','-File',str(root/entry)]
    first=subprocess.run(command,env=env,input='',capture_output=True,text=True,errors='replace',timeout=30)
    assert first.returncode==0,first.stdout+first.stderr
    old=trace.read_bytes();marker.unlink()
    second=subprocess.run(command,env=env,input='',capture_output=True,text=True,errors='replace',timeout=30)
    assert second.returncode==0,second.stdout+second.stderr
    assert marker.is_file() and trace.read_bytes()==old,'second run must execute verified cache without downloading'
    probe="$ErrorActionPreference='Stop'; $acl=[IO.Directory]::GetAccessControl([Environment]::GetEnvironmentVariable('PUBLIC_RESTRICT_CACHE','Process'),[Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner); $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])); if(-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid -or $rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $sid -or $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl' -or $rules[0].IsInherited) {exit 9}"
    checked=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-Command',probe],env=env,capture_output=True,text=True,timeout=30)
    assert checked.returncode==0,checked.stdout+checked.stderr
