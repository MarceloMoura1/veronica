const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const crypto = require('crypto');
const { resolveProjectPython, backendIdentityMatches } = require('./pythonRuntime');

// Use ANGLE D3D11 backend - more stable on Windows while keeping WebGL working
// This fixes "GPU state invalid after WaitForGetOffsetInRange" error
app.commandLine.appendSwitch('use-angle', 'd3d11');
app.commandLine.appendSwitch('enable-features', 'Vulkan');
app.commandLine.appendSwitch('ignore-gpu-blocklist');

let mainWindow;
let pythonProcess;
let pythonBackendStopped = false;
const projectRoot = path.resolve(__dirname, '..');
let selectedPythonExecutable = null;

function isProcessNotFoundError(error) {
    const output = [error && error.message, error && error.stdout, error && error.stderr]
        .filter(Boolean)
        .map((value) => value.toString())
        .join(' ')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase();

    return [
        'process not found',
        'processo nao foi encontrado',
        'nao foi encontrado',
        'no running instance',
        'nao ha instancia',
        'nenhuma instancia',
    ].some((message) => output.includes(message));
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1920,
        height: 1080,
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false, // For simple IPC/Socket.IO usage
        },
        backgroundColor: '#000000',
        frame: false, // Frameless for custom UI
        titleBarStyle: 'hidden',
        show: false, // Don't show until ready
    });

    // In dev, load Vite server. In prod, load index.html
    const isDev = process.env.NODE_ENV !== 'production';

    const loadFrontend = (retries = 3) => {
        const url = isDev ? 'http://localhost:5173' : null;
        const loadPromise = isDev
            ? mainWindow.loadURL(url)
            : mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));

        loadPromise
            .then(() => {
                console.log('Frontend loaded successfully!');
                windowWasShown = true;
                mainWindow.show();
                if (isDev) {
                    mainWindow.webContents.openDevTools();
                }
            })
            .catch((err) => {
                console.error(`Failed to load frontend: ${err.message}`);
                if (retries > 0) {
                    console.log(`Retrying in 1 second... (${retries} retries left)`);
                    setTimeout(() => loadFrontend(retries - 1), 1000);
                } else {
                    console.error('Failed to load frontend after all retries. Keeping window open.');
                    windowWasShown = true;
                    mainWindow.show(); // Show anyway so user sees something
                }
            });
    };

    loadFrontend();

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

function startPythonBackend(pythonExecutable) {
    const scriptPath = path.join(__dirname, '../backend/server.py');
    console.log(`Starting Python backend: ${scriptPath}`);
    console.log(`[PYTHON_RUNTIME] executable=${pythonExecutable}`);

    const instanceId = crypto.randomUUID();
    const startedProcess = spawn(pythonExecutable, ['-u', scriptPath], {
        cwd: path.join(__dirname, '../backend'),
        env: { ...process.env, ADA_BACKEND_INSTANCE_ID: instanceId },
    });
    pythonProcess = startedProcess;
    pythonBackendStopped = false;

    startedProcess.stdout.on('data', (data) => {
        console.log(`[Python]: ${data}`);
    });

    startedProcess.stderr.on('data', (data) => {
        console.error(`[Python Error]: ${data}`);
    });

    const clearStoppedProcess = (code, signal) => {
        if (pythonProcess !== startedProcess) return;
        pythonProcess = null;
        pythonBackendStopped = true;
        console.log(`Python backend stopped${signal ? ` (signal: ${signal})` : code !== null ? ` (code: ${code})` : ''}.`);
    };

    startedProcess.once('exit', clearStoppedProcess);
    startedProcess.once('close', clearStoppedProcess);
    return { process: startedProcess, instanceId };
}

app.whenReady().then(() => {
    ipcMain.on('window-minimize', () => {
        if (mainWindow) mainWindow.minimize();
    });

    ipcMain.on('window-maximize', () => {
        if (mainWindow) {
            if (mainWindow.isMaximized()) {
                mainWindow.unmaximize();
            } else {
                mainWindow.maximize();
            }
        }
    });

    ipcMain.on('window-close', () => {
        console.log('Window close requested by renderer.');
        if (mainWindow) mainWindow.close();
    });

    (async () => {
        try {
            selectedPythonExecutable = resolveProjectPython(projectRoot);
            console.log(`[PYTHON_RUNTIME] executable=${selectedPythonExecutable}`);
            const isTaken = await checkBackendPort(8000);
            if (isTaken) {
                let identity = null;
                try { identity = await getBackendStatus(); } catch (_) { /* reported below */ }
                const detail = identity
                    ? `pid=${identity.pid} executable=${identity.python_executable}`
                    : 'identity unavailable';
                throw new Error(
                    `Port 8000 is already occupied by a backend Electron did not start (${detail}). ` +
                    'Refusing to connect; the existing process was not terminated.'
                );
            }
            const started = startPythonBackend(selectedPythonExecutable);
            await waitForBackend({
                expectedInstanceId: started.instanceId,
                expectedExecutable: selectedPythonExecutable,
            });
            createWindow();
        } catch (error) {
            console.error(`[PYTHON_RUNTIME] startup_error=${error.message}`);
            dialog.showErrorBox('Python backend startup failed', error.message);
            app.quit();
        }
    })();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

function checkBackendPort(port) {
    return new Promise((resolve) => {
        const net = require('net');
        const server = net.createServer();
        server.once('error', (err) => {
            if (err.code === 'EADDRINUSE') {
                resolve(true);
            } else {
                resolve(false);
            }
        });
        server.once('listening', () => {
            server.close();
            resolve(false);
        });
        server.listen(port);
    });
}

function waitForBackend() {
    const options = arguments[0] || {};
    return new Promise((resolve, reject) => {
        const check = () => {
            getBackendStatus().then((identity) => {
                if (!backendIdentityMatches(identity, {
                    instanceId: options.expectedInstanceId,
                    executable: options.expectedExecutable,
                })) {
                    reject(new Error(
                        `Backend identity mismatch: expected instance=${options.expectedInstanceId} ` +
                        `executable=${options.expectedExecutable}; received instance=${identity.instance_id} ` +
                        `pid=${identity.pid} executable=${identity.python_executable}`
                    ));
                    return;
                }
                if (identity.status === 'running') {
                    console.log('Backend is ready!');
                    resolve();
                } else {
                    console.log('Backend not ready, retrying...');
                    setTimeout(check, 1000);
                }
            }).catch(() => {
                console.log('Waiting for backend...');
                setTimeout(check, 1000);
            });
        };
        check();
    });
}

function getBackendStatus() {
    return new Promise((resolve, reject) => {
        http.get('http://127.0.0.1:8000/status', (res) => {
            let body = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => { body += chunk; });
            res.on('end', () => {
                if (res.statusCode !== 200) {
                    reject(new Error(`Backend status returned HTTP ${res.statusCode}`));
                    return;
                }
                try { resolve(JSON.parse(body)); }
                catch (error) { reject(new Error(`Invalid backend status response: ${error.message}`)); }
            });
        }).on('error', reject);
    });
}

let windowWasShown = false;

app.on('window-all-closed', () => {
    console.log(`All windows closed (windowWasShown=${windowWasShown}).`);
    // Only quit if the window was actually shown at least once
    // This prevents quitting during startup if window creation fails
    if (process.platform !== 'darwin' && windowWasShown) {
        app.quit();
    } else if (!windowWasShown) {
        console.log('Window was never shown - keeping app alive to allow retries');
    }
});

app.on('will-quit', () => {
    console.log('App closing...');
    if (!pythonProcess) {
        if (pythonBackendStopped) {
            console.log('Python backend already stopped.');
        }
        return;
    }

    if (pythonProcess.exitCode !== null || pythonProcess.signalCode !== null) {
        pythonProcess = null;
        pythonBackendStopped = true;
        console.log('Python backend already stopped.');
        return;
    }

    const processToStop = pythonProcess;
    if (process.platform === 'win32') {
        // Windows: Force kill the active process tree synchronously.
        try {
            const { execSync } = require('child_process');
            execSync(`taskkill /pid ${processToStop.pid} /f /t`, { stdio: 'pipe' });
            console.log('Python backend stopped.');
        } catch (error) {
            if (isProcessNotFoundError(error)) {
                console.log('Python backend already stopped.');
            } else {
                console.error('Failed to stop Python backend:', error.message);
            }
        }
    } else {
        try {
            processToStop.kill('SIGKILL');
            console.log('Python backend stopped.');
        } catch (error) {
            if (error.code === 'ESRCH') {
                console.log('Python backend already stopped.');
            } else {
                console.error('Failed to stop Python backend:', error.message);
            }
        }
    }

    if (pythonProcess === processToStop) {
        pythonProcess = null;
        pythonBackendStopped = true;
    }
});
