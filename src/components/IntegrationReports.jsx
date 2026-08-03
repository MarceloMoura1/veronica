import React, { useEffect, useState } from 'react';
import { ArrowLeft, ServerCog } from 'lucide-react';
import './IntegrationCenter.css';

const formatTime = (value) => value ? new Date(value).toLocaleString('pt-BR') : 'Indisponível';

function ReportList({ items, empty }) {
    if (!items?.length) return <p className="report-empty">{empty}</p>;
    return <div className="report-list">{items.map((item, index) => <div key={`${item.timestamp}-${index}`}><time>{formatTime(item.timestamp)}</time><strong>{item.event}</strong><span>{item.message}</span></div>)}</div>;
}

function IntegrationReports({ socket }) {
    const [integrations, setIntegrations] = useState([]);
    const [selectedId, setSelectedId] = useState(null);
    const [payload, setPayload] = useState(null);

    useEffect(() => {
        const onRegistry = ({ integrations: items = [] } = {}) => setIntegrations(Array.isArray(items) ? items : []);
        const onReports = (next) => setPayload(next);
        const onRealtime = (next) => {
            if (next?.reports?.integration?.id === selectedId) setPayload(next.reports);
        };
        socket.on('integration_registry', onRegistry);
        socket.on('integration_reports', onReports);
        socket.on('integration_realtime_update', onRealtime);
        socket.emit('get_integrations');
        return () => {
            socket.off('integration_registry', onRegistry);
            socket.off('integration_reports', onReports);
            socket.off('integration_realtime_update', onRealtime);
        };
    }, [socket, selectedId]);

    useEffect(() => {
        if (selectedId) socket.emit('get_integration_reports', { integration_id: selectedId });
    }, [socket, selectedId]);

    if (!selectedId) return (
        <main className="integration-center" aria-labelledby="reports-title">
            <header className="integration-center__header"><p>VERÔNICA / INTEGRATION MANAGER</p><h2 id="reports-title">Relatórios</h2></header>
            <div className="integration-list">
                {integrations.map((integration) => <button key={integration.id} type="button" className="integration-list__item" onClick={() => setSelectedId(integration.id)}><span className="integration-list__icon"><ServerCog size={20} /></span><span className="integration-list__identity"><strong>{integration.name}</strong><small>{integration.provider}</small></span></button>)}
                {!integrations.length && <div className="integration-list__empty">Nenhuma integração registrada.</div>}
            </div>
        </main>
    );

    const reports = payload?.reports || {};
    return (
        <main className="integration-center integration-center--detail" aria-labelledby="report-detail-title">
            <button type="button" className="integration-back" onClick={() => { setSelectedId(null); setPayload(null); }}><ArrowLeft size={16} /> Relatórios</button>
            <header className="integration-detail__header"><div><p>{payload?.integration?.provider}</p><h2 id="report-detail-title">{payload?.integration?.name || 'Integração'}</h2></div></header>
            <section className="integration-section"><h3>Últimos erros</h3><ReportList items={reports.errors} empty="Nenhum erro registrado." /></section>
            <section className="integration-section"><h3>Últimos avisos</h3><ReportList items={reports.warnings} empty="Nenhum aviso registrado." /></section>
            <section className="integration-section"><h3>Últimos eventos</h3><ReportList items={reports.events} empty="Nenhum evento registrado." /></section>
        </main>
    );
}

export default IntegrationReports;
