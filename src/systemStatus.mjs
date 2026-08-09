export const HISTORY_LIMIT = 60;

export function appendHistory(history = [], value, limit = HISTORY_LIMIT) {
    if (!Number.isFinite(value)) return history.slice(-limit);
    return [...history, value].slice(-limit);
}

export function appendSystemHistories(histories, status, limit = HISTORY_LIMIT) {
    const network = status?.network;
    return {
        cpu: appendHistory(histories.cpu, status?.cpu?.percent, limit),
        memory: appendHistory(histories.memory, status?.memory?.percent, limit),
        application: appendHistory(histories.application, status?.application?.memory_bytes, limit),
        disk: appendHistory(histories.disk, status?.disk?.percent, limit),
        network: appendHistory(histories.network,
            network ? (network.download_bps || 0) + (network.upload_bps || 0) : null, limit),
        gpu: appendHistory(histories.gpu, status?.gpu?.available ? status.gpu.percent : null, limit),
    };
}

export function formatBytes(value) {
    if (!Number.isFinite(value)) return '—';
    if (value === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const index = Math.min(Math.floor(Math.log(Math.abs(value)) / Math.log(1024)), units.length - 1);
    const amount = value / (1024 ** index);
    return `${amount.toFixed(index >= 3 ? 1 : amount >= 10 ? 0 : 1)} ${units[index]}`;
}

export function formatRate(value) {
    return Number.isFinite(value) ? `${formatBytes(value)}/s` : '—';
}

export function formatUptime(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return [days ? `${days}d` : '', `${hours}h`, `${minutes}m`].filter(Boolean).join(' ');
}

export function statusTone(status) {
    return ['healthy', 'warning', 'critical'].includes(status) ? status : 'unavailable';
}
