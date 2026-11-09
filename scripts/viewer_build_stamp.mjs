import {createHash} from 'node:crypto';
import {existsSync,readdirSync,readFileSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve,relative} from 'node:path';
import {fileURLToPath} from 'node:url';

export function fingerprint(root){
 const paths=[];
 function walk(folder){if(!existsSync(folder))return;for(const item of readdirSync(folder,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){if(item.isSymbolicLink())continue;const path=join(folder,item.name);item.isDirectory()?walk(path):paths.push(path);}}
 walk(join(root,'visualization/src'));walk(join(root,'visualization/public'));
 for(const name of ['visualization/index.html','visualization/vite.config.js','visualization/package.json','visualization/package-lock.json','scripts/viewer_build_stamp.mjs'])if(existsSync(join(root,name)))paths.push(join(root,name));
 const hash=createHash('sha256');for(const path of paths.sort()){hash.update(relative(root,path));hash.update(readFileSync(path));}
 return hash.digest('hex');
}
export function writeStamp(root,dist){writeFileSync(join(dist,'viewer-build.json'),JSON.stringify({source_sha256:fingerprint(root),schema_version:'viewer_build.v1'}));}
const own=fileURLToPath(import.meta.url);
if(process.argv[1]&&resolve(process.argv[1])===own){
 const root=resolve(dirname(own),'..'),dist=join(root,'visualization/dist');
 try{const stamp=JSON.parse(readFileSync(join(dist,'viewer-build.json')));process.exitCode=existsSync(join(dist,'index.html'))&&stamp.source_sha256===fingerprint(root)?0:1;}catch{process.exitCode=1;}
}
