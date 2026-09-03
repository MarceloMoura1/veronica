const PROJECT_SELECT_FOLDER_CHANNEL = 'project-select-folder';

const registeredIpcMains = new WeakSet();

function createSelectFolderHandler({ dialog, getParentWindow = () => null, logger = console }) {
    return async function selectProjectFolder() {
        logger.log('[PROJECT_IPC] select_folder requested');
        try {
            const options = {
                title: 'Selecionar pasta do projeto',
                buttonLabel: 'Usar esta pasta',
                properties: ['openDirectory'],
            };
            const parentWindow = getParentWindow();
            const result = parentWindow
                ? await dialog.showOpenDialog(parentWindow, options)
                : await dialog.showOpenDialog(options);

            if (result.canceled || !result.filePaths.length) {
                logger.log('[PROJECT_IPC] select_folder cancelled');
                return { ok: false, cancelled: true };
            }

            logger.log('[PROJECT_IPC] select_folder success');
            return { ok: true, path: result.filePaths[0] };
        } catch (error) {
            logger.error(`[PROJECT_IPC] select_folder failed: ${error.message}`);
            return {
                ok: false,
                error: {
                    code: 'folder_picker_failed',
                    message: 'Não foi possível abrir o seletor de pasta.',
                },
            };
        }
    };
}

function registerProjectIpcHandlers({ ipcMain, dialog, getParentWindow, logger = console }) {
    if (registeredIpcMains.has(ipcMain)) return false;
    ipcMain.handle(
        PROJECT_SELECT_FOLDER_CHANNEL,
        createSelectFolderHandler({ dialog, getParentWindow, logger }),
    );
    registeredIpcMains.add(ipcMain);
    logger.log(`[PROJECT_IPC] registered channel=${PROJECT_SELECT_FOLDER_CHANNEL}`);
    return true;
}

module.exports = {
    PROJECT_SELECT_FOLDER_CHANNEL,
    createSelectFolderHandler,
    registerProjectIpcHandlers,
};
