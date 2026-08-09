import React, { useEffect, useState } from 'react';
import { Activity, ChevronDown, Cpu, Database, Gauge, HardDrive, MemoryStick, Network, UserRound } from 'lucide-react';
import SystemMetric from './SystemMetric';
import { appendSystemHistories, formatBytes, formatRate, formatUptime, statusTone } from '../systemStatus.mjs';
import './SystemStatusPanel.css';

const EMPTY_HISTORIES = { cpu: [], memory: [], disk: [], network: [], gpu: [] };

export default function SystemStatusPanel({ status, connected }) {
    const [expanded, setExpanded] = useState(false);
    const [histories, setHistories] = useState(EMPTY_HISTORIES);

    useEffect(() => {
        if (status?.available) setHistories(current => appendSystemHistories(current, status));
    }, [status]);

    const available = Boolean(status?.available);
    const tone = statusTone(status?.overall_status);
    const memory = status?.memory;
    const disk = status?.disk;
    const network = status?.network;
    const gpu = status?.gpu;

    return (
        <aside className={`system-panel system-panel--${tone} ${expanded ? 'system-panel--expanded' : ''}`} aria-label="Status do computador">
            <button className="system-panel__mobile-toggle" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
                <Activity size={15} />
                <span>{available ? status.status_text : 'Status indisponível'}</span>
                <ChevronDown size={14} />
            </button>
            <div className="system-panel__content">
                <header className="system-panel__header">
                    <div>
                        <span className="system-panel__eyebrow"><Gauge size={12} /> STATUS DO SISTEMA</span>
                        <h2>{available ? status.status_text : 'Status indisponível'}</h2>
                        <p>{available ? 'Telemetria local em tempo real' : 'Dados temporariamente indisponíveis'}</p>
                    </div>
                    <span className="system-panel__health-dot" title={tone} />
                </header>

                <div className="system-panel__metrics">
                    <SystemMetric icon={Cpu} label="CPU" value={available ? `${status.cpu.percent.toFixed(1)}%` : '—'}
                        detail="uso atual" history={histories.cpu} />
                    <SystemMetric icon={MemoryStick} label="RAM" value={memory ? `${memory.percent.toFixed(1)}%` : '—'}
                        detail={memory ? `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}` : 'indisponível'} history={histories.memory} />
                    <SystemMetric icon={HardDrive} label="DISCO" value={disk ? `${disk.percent.toFixed(1)}%` : '—'}
                        detail={disk ? `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)} · ${disk.mount}` : 'indisponível'} history={histories.disk} />
                    <SystemMetric icon={Network} label="REDE" value={network ? `↓ ${formatRate(network.download_bps)}` : '—'}
                        detail={network ? `↑ ${formatRate(network.upload_bps)}` : 'indisponível'} history={histories.network} />
                </div>

                {gpu?.available && (
                    <div className="system-panel__gpu">
                        <SystemMetric icon={Database} label="GPU" value={`${gpu.percent.toFixed(1)}%`}
                            detail={`${gpu.name} · ${gpu.temperature_c.toFixed(0)}°C · ${formatBytes(gpu.memory_used_bytes)} VRAM`} history={histories.gpu} tone="violet" />
                    </div>
                )}

                <dl className="system-panel__facts">
                    <div><dt>UPTIME</dt><dd>{formatUptime(status?.uptime_seconds)}</dd></div>
                    <div><dt>SISTEMA</dt><dd>{status?.system?.hostname || '—'} · {status?.system?.os || '—'}</dd></div>
                </dl>

                <footer className="system-panel__user">
                    <span className="system-panel__avatar"><UserRound size={17} /></span>
                    <span><strong>{status?.system?.user || 'Usuário local'}</strong><small>Computador local</small></span>
                    <span className={`system-panel__online ${connected ? 'is-online' : ''}`}>{connected ? 'ONLINE' : 'OFFLINE'}</span>
                </footer>
            </div>
        </aside>
    );
}
