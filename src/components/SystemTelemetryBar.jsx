import React, { memo, useEffect, useState } from 'react';
import Sparkline from './Sparkline';
import { appendSystemHistories, formatBytes, formatRate, statusTone } from '../systemStatus.mjs';
import './SystemTelemetryBar.css';

const EMPTY = { cpu: [], memory: [], application: [], disk: [], network: [], gpu: [] };

function Metric({ className = '', label, value, history, title }) {
    return (
        <div className={`telemetry-metric ${className}`} title={title} tabIndex="0">
            <span className="telemetry-metric__label">{label}</span>
            <strong>{value}</strong>
            {history && <Sparkline values={history} label={`${label}, últimos 60 segundos`} />}
        </div>
    );
}

function SystemTelemetryBar({ socket, connected }) {
    const [status, setStatus] = useState(null);
    const [histories, setHistories] = useState(EMPTY);

    useEffect(() => {
        const update = (next) => {
            setStatus(next);
            if (next?.available) setHistories(current => appendSystemHistories(current, next));
        };
        socket.on('system_status', update);
        return () => socket.off('system_status', update);
    }, [socket]);

    const available = Boolean(status?.available);
    const tone = statusTone(status?.overall_status);
    const cpu = status?.cpu;
    const memory = status?.memory;
    const application = status?.application;
    const disk = status?.disk;
    const network = status?.network;
    const gpu = status?.gpu;
    const appScope = application?.memory_scope === 'backend_only' ? 'backend' : 'Verônica';

    return (
        <section className="telemetry-bar" aria-label="Telemetria do sistema" style={{ WebkitAppRegion: 'no-drag' }}>
            <div className={`telemetry-health telemetry-health--${tone}`} title={available ? status.status_text : connected ? 'Aguardando telemetria' : 'Backend offline'}>
                <span /> <b>SISTEMA</b>
            </div>
            <Metric label="CPU" value={cpu ? `${cpu.percent.toFixed(1)}%` : '—'} history={histories.cpu}
                title="Uso atual do processador" />
            <Metric label="RAM" value={memory ? `${memory.percent.toFixed(1)}%` : '—'} history={histories.memory}
                title={memory ? `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}` : 'Indisponível'} />
            <Metric className="telemetry-metric--application" label={application?.memory_scope === 'backend_only' ? 'BACKEND' : 'VERÔNICA'}
                value={Number.isFinite(application?.memory_bytes) ? formatBytes(application.memory_bytes) : '—'} history={histories.application}
                title={Number.isFinite(application?.memory_bytes) ? `${formatBytes(application.memory_bytes)} em ${application.process_count} processo(s) · escopo: ${appScope}` : 'Memória da aplicação indisponível'} />
            <Metric className="telemetry-metric--disk" label="DISCO" value={disk ? `${disk.percent.toFixed(1)}%` : '—'}
                title={disk ? `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)} · volume ${disk.mount}` : 'Indisponível'} />
            <Metric className="telemetry-metric--network" label="REDE"
                value={network ? `↓${formatRate(network.download_bps)} ↑${formatRate(network.upload_bps)}` : '—'} history={histories.network}
                title={network ? `Download ${formatRate(network.download_bps)} · Upload ${formatRate(network.upload_bps)}` : 'Indisponível'} />
            {gpu?.available && <Metric className="telemetry-metric--gpu" label="GPU" value={`${gpu.percent.toFixed(0)}% · ${gpu.temperature_c.toFixed(0)}°C`} history={histories.gpu}
                title={`${gpu.name} · VRAM ${formatBytes(gpu.memory_used_bytes)} / ${formatBytes(gpu.memory_total_bytes)} · ${gpu.temperature_c.toFixed(0)}°C`} />}
        </section>
    );
}

export default memo(SystemTelemetryBar);
