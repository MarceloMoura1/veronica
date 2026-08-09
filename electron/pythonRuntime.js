const fs = require('fs');
const path = require('path');

function resolveProjectPython(projectRoot, options = {}) {
    const platform = options.platform || process.platform;
    const existsSync = options.existsSync || fs.existsSync;
    const pathApi = platform === 'win32' ? path.win32 : path.posix;
    const relative = platform === 'win32'
        ? ['.venv', 'Scripts', 'python.exe']
        : ['.venv', 'bin', 'python'];
    const executable = pathApi.resolve(projectRoot, ...relative);
    if (!existsSync(executable)) {
        throw new Error(`Project virtualenv Python not found: ${executable}`);
    }
    return executable;
}

function sameExecutable(first, second, platform = process.platform) {
    if (!first || !second) return false;
    const pathApi = platform === 'win32' ? path.win32 : path.posix;
    const normalizedFirst = pathApi.normalize(pathApi.resolve(first));
    const normalizedSecond = pathApi.normalize(pathApi.resolve(second));
    return platform === 'win32'
        ? normalizedFirst.toLowerCase() === normalizedSecond.toLowerCase()
        : normalizedFirst === normalizedSecond;
}

function backendIdentityMatches(identity, expected, platform = process.platform) {
    return Boolean(
        identity && expected &&
        identity.instance_id === expected.instanceId &&
        sameExecutable(identity.python_executable, expected.executable, platform)
    );
}

module.exports = { resolveProjectPython, sameExecutable, backendIdentityMatches };
