import fs from 'node:fs';
import path from 'node:path';

const privateDirectories = new Set(['_operations', '_process_logs', '_batches', '__pycache__', 'node_modules']);
const privateSuffixes = new Set(['.db', '.sqlite', '.sqlite3', '.pkl', '.pickle', '.pem', '.key', '.log']);

export function isPublicArtifact(filePath, rootDir) {
  const relative = path.relative(rootDir, filePath);
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return false;
  const parts = relative.split(path.sep).filter(Boolean);
  if (parts.some((part) => part.startsWith('.') || privateDirectories.has(part.toLowerCase()))) return false;
  const name = path.basename(filePath).toLowerCase();
  if (privateSuffixes.has(path.extname(name)) || name.endsWith('.private.json')
      || name.endsWith('.agent_env.json') || name.includes('.sqlite-')) return false;
  try {
    const resolved = path.relative(fs.realpathSync(rootDir), fs.realpathSync(filePath));
    return resolved !== '..' && !resolved.startsWith(`..${path.sep}`) && !path.isAbsolute(resolved);
  } catch {
    return false;
  }
}
