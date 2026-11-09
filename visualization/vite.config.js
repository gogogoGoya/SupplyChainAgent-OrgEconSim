import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
import {realpathSync} from 'node:fs';
import {resolve} from 'node:path';
import {writeStamp} from '../scripts/viewer_build_stamp.mjs';
const viewerRoot=fileURLToPath(new URL('.',import.meta.url));
const dependencies=realpathSync(fileURLToPath(new URL('./node_modules',import.meta.url)));
const target=process.env.OPERATIONS_API_PROXY_TARGET || 'http://127.0.0.1:8000';
const proxy=Object.fromEntries(['/operations','/workspace_jobs','/workspace_multi','/simulation_runs'].map(prefix=>[prefix,{target,changeOrigin:true}]));
let outputRoot;
export default defineConfig({
  plugins:[react(),{name:'viewer-build-stamp',configResolved(config){outputRoot=config.command==='build'?resolve(config.root,config.build.outDir):null;},closeBundle(){if(outputRoot)writeStamp(resolve(viewerRoot,'..'),outputRoot);}}],
  optimizeDeps:{include:['echarts/core','echarts/charts','echarts/components','echarts/renderers']},
  server:{host:'127.0.0.1',port:3000,strictPort:true,proxy,
    fs:{strict:true,allow:[viewerRoot,dependencies],deny:['.env','.env.*','*.{crt,pem,key}','**/*.private.json','**/*.agent_env.json','**/workspace_jobs/**','**/workspace_multi/**','**/simulation_runs/**']},
    watch:{ignored:['**/workspace_jobs/**','**/workspace_multi/**','**/simulation_runs/**','**/public_results/**','**/agent/runtime/**']}},
  preview:{host:'127.0.0.1',strictPort:true,proxy},
  build:{rollupOptions:{output:{manualChunks(id){if(id.includes('/node_modules/echarts/')||id.includes('/node_modules/zrender/'))return 'charts';}}}},
});
