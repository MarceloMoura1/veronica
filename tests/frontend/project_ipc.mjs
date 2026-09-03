import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';

import { PROJECT_SELECT_FOLDER_CHANNEL, selectProjectFolder } from '../../src/components/projects/projectFolderPicker.mjs';

const require = createRequire(import.meta.url);
const {
    PROJECT_SELECT_FOLDER_CHANNEL: MAIN_CHANNEL,
    createSelectFolderHandler,
    registerProjectIpcHandlers,
} = require('../../electron/projectIpc.js');

const silentLogger = { log() {}, error() {} };

test('main and renderer use the exact project folder channel', () => {
    assert.equal(MAIN_CHANNEL, 'project-select-folder');
    assert.equal(PROJECT_SELECT_FOLDER_CHANNEL, MAIN_CHANNEL);
});

test('registers the handler exactly once', () => {
    const calls = [];
    const ipcMain = { handle: (...args) => calls.push(args) };
    const options = { ipcMain, dialog: {}, getParentWindow: () => null, logger: silentLogger };
    assert.equal(registerProjectIpcHandlers(options), true);
    assert.equal(registerProjectIpcHandlers(options), false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], MAIN_CHANNEL);
});

test('folder dialog returns a structured selected path', async () => {
    const parent = {};
    let received;
    const handler = createSelectFolderHandler({
        dialog: { showOpenDialog: async (...args) => { received = args; return { canceled: false, filePaths: ['C:\\workspace'] }; } },
        getParentWindow: () => parent,
        logger: silentLogger,
    });
    assert.deepEqual(await handler(), { ok: true, path: 'C:\\workspace' });
    assert.equal(received[0], parent);
    assert.deepEqual(received[1].properties, ['openDirectory']);
});

test('folder dialog cancellation is normal and structured', async () => {
    const handler = createSelectFolderHandler({
        dialog: { showOpenDialog: async () => ({ canceled: true, filePaths: [] }) },
        logger: silentLogger,
    });
    assert.deepEqual(await handler(), { ok: false, cancelled: true });
});

test('folder dialog failure returns a structured safe error', async () => {
    const handler = createSelectFolderHandler({
        dialog: { showOpenDialog: async () => { throw new Error('native failure'); } },
        logger: silentLogger,
    });
    assert.deepEqual(await handler(), {
        ok: false,
        error: { code: 'folder_picker_failed', message: 'Não foi possível abrir o seletor de pasta.' },
    });
});

test('renderer invokes once while a selection is already pending', async () => {
    let resolveInvoke;
    let calls = 0;
    const electron = { ipcRenderer: { invoke(channel) {
        calls += 1;
        assert.equal(channel, MAIN_CHANNEL);
        return new Promise(resolve => { resolveInvoke = resolve; });
    } } };

    const first = selectProjectFolder(electron);
    const second = selectProjectFolder(electron);
    assert.equal(first, second);
    assert.equal(calls, 1);
    resolveInvoke({ ok: true, path: 'C:\\selected' });
    assert.equal(await first, 'C:\\selected');
});

test('renderer treats cancellation as no selection and exposes structured errors', async () => {
    assert.equal(await selectProjectFolder({ ipcRenderer: { invoke: async () => ({ ok: false, cancelled: true }) } }), null);
    await assert.rejects(
        selectProjectFolder({ ipcRenderer: { invoke: async () => ({ ok: false, error: { message: 'Falha clara' } }) } }),
        /Falha clara/,
    );
});
