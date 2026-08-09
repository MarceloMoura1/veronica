import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { resolveProjectPython, sameExecutable, backendIdentityMatches } = require('../../electron/pythonRuntime.js');

test('resolves project venv Python on Windows without consulting PATH', () => {
    const seen = [];
    const result = resolveProjectPython('C:\\work\\ada', {
        platform: 'win32',
        existsSync: (candidate) => { seen.push(candidate); return true; },
    });

    assert.equal(result, 'C:\\work\\ada\\.venv\\Scripts\\python.exe');
    assert.deepEqual(seen, [result]);
});

test('fails clearly when project virtualenv is absent', () => {
    assert.throws(
        () => resolveProjectPython('C:\\work\\ada', { platform: 'win32', existsSync: () => false }),
        /Project virtualenv Python not found: C:\\work\\ada\\\.venv\\Scripts\\python\.exe/,
    );
});

test('compares Windows executable paths case-insensitively', () => {
    assert.equal(
        sameExecutable(
            'C:\\Projetos\\ada_custom\\.venv\\Scripts\\python.exe',
            'c:\\projetos\\ADA_CUSTOM\\.venv\\Scripts\\PYTHON.EXE',
            'win32',
        ),
        true,
    );
});

test('backend identity requires both launch token and virtualenv executable', () => {
    const expected = {
        instanceId: 'launch-123',
        executable: 'C:\\work\\ada\\.venv\\Scripts\\python.exe',
    };
    assert.equal(backendIdentityMatches({
        instance_id: 'launch-123',
        python_executable: 'c:\\WORK\\ada\\.venv\\Scripts\\PYTHON.EXE',
    }, expected, 'win32'), true);
    assert.equal(backendIdentityMatches({
        instance_id: 'another-process',
        python_executable: expected.executable,
    }, expected, 'win32'), false);
});
