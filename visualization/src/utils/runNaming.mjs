/* Recognize published archive prefixes and historical experiment suffixes. */
export const extractRunExperimentTag = (value) => {
  const name = String(value || '').split('/').filter(Boolean).pop() || '';
  if (!name) {
    return '';
  }
  const workspaceJobMatch = name.match(
    /^run_[A-Za-z0-9]+__started_\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}_(.+)$/
  );
  if (workspaceJobMatch?.[1]) {
    return workspaceJobMatch[1];
  }
  const legacyMatch = name.match(/^run_\d{4}-\d{2}-\d{2}_\d{6}_(.+)$/);
  if (legacyMatch?.[1]) {
    return legacyMatch[1];
  }
  // Published archive names put scenario, arm, and optional conditions before the run ID.
  const prefixMatch = name.match(
    /^((?:FD[1-9]\d*|EM-[A-Z]{2}|LH-SS)_[A-Za-z0-9][A-Za-z0-9_-]*__(?:AG-OC|AG|MA|RB|NA|NC|SA)(?:__[A-Za-z0-9][A-Za-z0-9_-]*)*)__run_[A-Za-z0-9]+$/
  );
  return prefixMatch?.[1] || '';
};

export const getRunExperimentTag = (...values) => {
  for (const value of values) {
    const tag = extractRunExperimentTag(value);
    if (tag) {
      return tag;
    }
  }
  return '';
};

