export const PROJECT_SELECT_FOLDER_CHANNEL = 'project-select-folder';

let pendingSelection = null;

export function selectProjectFolder(electron = window.require('electron')) {
    if (pendingSelection) return pendingSelection;

    pendingSelection = electron.ipcRenderer.invoke(PROJECT_SELECT_FOLDER_CHANNEL)
        .then((result) => {
            if (result?.ok) return result.path;
            if (result?.cancelled) return null;
            throw new Error(result?.error?.message || 'Não foi possível abrir o seletor de pasta.');
        })
        .finally(() => { pendingSelection = null; });

    return pendingSelection;
}
