const net = require('net');

const port = 5173;
const host = '127.0.0.1';
const server = net.createServer();

server.once('error', (error) => {
    if (error.code === 'EADDRINUSE') {
        console.error(`[DEV_STARTUP] Port ${port} is already occupied. Refusing to attach Electron to an older Vite process.`);
    } else {
        console.error(`[DEV_STARTUP] Unable to validate port ${port}: ${error.message}`);
    }
    process.exitCode = 1;
});

server.once('listening', () => {
    server.close(() => {
        console.log(`[DEV_STARTUP] Port ${port} is available for this Vite instance.`);
    });
});

server.listen(port, host);
