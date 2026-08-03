import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, RefreshCw, ServerCog, Zap } from 'lucide-react';
import './IntegrationCenter.css';

const statusLabels = {
    active: 'Conectado', inactive: 'Inativo', error: 'Erro', checking: 'Verificando', not_configured: 'Não configurado'
};
const periods = [
    ['today', 'Hoje'], ['yesterday', 'Ontem'], ['last_7_days', 'Últimos 7 dias'],
    ['this_month', 'Este mês'], ['custom', 'Personalizado']
];
const formatNumber = (value) => Number.isFinite(value) ? value.toLocaleString('pt-BR') : 'Indisponível';
const formatTime = (value) => value ? new Date(value).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : 'Indisponível';

function Status({ value }) {
    return <span className={`integration-status integration-status--${value || 'inactive'}`}><i />{statusLabels[value] || 'Indisponível'}</span>;
}

function IntegrationCenter({ socket }) {
    const [integrations, setIntegrations] = useState([]);
    const [selectedId, setSelectedId] = useState(null);
    const [details, setDetails] = useState(null);
    const [period, setPeriod] = useState('today');
    const [customDates, setCustomDates] = useState({ start_date: '', end_date: '' });
    const selected = useMemo(() => integrations.find((item) => item.id === selectedId), [integrations, selectedId]);

    const requestDetails = (nextPeriod = period, dates = customDates) => {
        if (!selectedId) return;
        socket.emit('get_integration_details', {
            integration_id: selectedId,
            period: nextPeriod,
            ...(nextPeriod === 'custom' ? dates : {})
        });
    };

    useEffect(() => {
        const onRegistry = ({ integrations: items = {} } = {}) => {
            const next = Array.isArray(items) ? items : [];
            setIntegrations(next);
            setDetails((current) => {
                const state = next.find((item) => item.id === current?.id);
                return state ? { ...current, ...state } : current;
            });
        };
        const onDetails = (payload) => setDetails(payload);
        const onTest = (payload) => setDetails((current) => current?.id === payload?.id ? { ...current, ...payload } : current);
        const onRealtime = (payload) => {
            const update = payload?.details;
            setDetails((current) => current?.id === update?.id ? { ...current, ...update } : current);
        };
        socket.on('integration_registry', onRegistry);
        socket.on('integration_details', onDetails);
        socket.on('integration_test_result', onTest);
        socket.on('integration_realtime_update', onRealtime);
        socket.emit('get_integrations');
        return () => {
            socket.off('integration_registry', onRegistry);
            socket.off('integration_details', onDetails);
            socket.off('integration_test_result', onTest);
            socket.off('integration_realtime_update', onRealtime);
        };
    }, [socket]);

    useEffect(() => { if (selectedId) requestDetails(); }, [selectedId]);

    if (!selectedId) return (
        <main className="integration-center" aria-labelledby="integrations-title">
            <header className="integration-center__header"><p>VERÔNICA / INTEGRATION MANAGER</p><h2 id="integrations-title">Integrações</h2></header>
            <div className="integration-list">
                {integrations.map((integration) => (
                    <button key={integration.id} type="button" className="integration-list__item" onClick={() => setSelectedId(integration.id)}>
                        <span className="integration-list__icon"><ServerCog size={20} /></span>
                        <span className="integration-list__identity"><strong>{integration.name}</strong><small>{integration.provider}</small></span>
                        <Status value={integration.status} />
                    </button>
                ))}
                {!integrations.length && <div className="integration-list__empty">Aguardando IntegrationManager…</div>}
            </div>
        </main>
    );

    const current = details || selected || {};
    const usage = current.usage || {};
    const metadata = current.metadata || {};
    const choosePeriod = (nextPeriod) => {
        setPeriod(nextPeriod);
        if (nextPeriod !== 'custom') requestDetails(nextPeriod);
    };
    const actionPayload = { integration_id: selectedId, period, ...(period === 'custom' ? customDates : {}) };

    return (
        <main className="integration-center integration-center--detail" aria-labelledby="integration-detail-title">
            <button type="button" className="integration-back" onClick={() => { setSelectedId(null); setDetails(null); }}><ArrowLeft size={16} /> Integrações</button>
            <header className="integration-detail__header"><div><p>{current.provider}</p><h2 id="integration-detail-title">{current.name}</h2></div><Status value={current.status} /></header>

            <section className="integration-section">
                <h3>Status</h3>
                <dl className="integration-facts">
                    <div><dt>Status</dt><dd><Status value={current.status} /></dd></div>
                    <div><dt>Provider</dt><dd>{current.provider || 'Indisponível'}</dd></div>
                    <div><dt>Modelo</dt><dd>{metadata.main_model || 'Indisponível'}</dd></div>
                    <div><dt>Última verificação</dt><dd>{formatTime(current.last_check)}</dd></div>
                    <div><dt>Latência</dt><dd>{current.latency_ms == null ? 'Indisponível' : `${current.latency_ms} ms`}</dd></div>
                    <div><dt>API</dt><dd>{current.api_key_configured ? 'Configurada' : 'Não configurada'}</dd></div>
                    <div><dt>Versão SDK</dt><dd>{metadata.sdk_version ? `${metadata.sdk_name} ${metadata.sdk_version}` : 'Indisponível'}</dd></div>
                </dl>
            </section>

            <section className="integration-section">
                <h3>Telemetria</h3>
                <div className="integration-metrics">
                    <div><span>Tokens Entrada</span><strong>{formatNumber(usage.input_tokens)}</strong></div>
                    <div><span>Tokens Saída</span><strong>{formatNumber(usage.output_tokens)}</strong></div>
                    <div><span>Tokens Totais</span><strong>{formatNumber(usage.total_tokens)}</strong></div>
                    <div><span>Requisições</span><strong>{formatNumber(usage.requests)}</strong></div>
                    <div><span>Erros</span><strong>{formatNumber(usage.errors)}</strong></div>
                </div>
            </section>

            <section className="integration-section">
                <h3>Filtros</h3>
                <div className="integration-periods">{periods.map(([value, label]) => <button key={value} type="button" className={period === value ? 'active' : ''} onClick={() => choosePeriod(value)}>{label}</button>)}</div>
                {period === 'custom' && <div className="integration-custom-dates"><input type="date" value={customDates.start_date} onChange={(event) => setCustomDates({ ...customDates, start_date: event.target.value })} /><input type="date" value={customDates.end_date} onChange={(event) => setCustomDates({ ...customDates, end_date: event.target.value })} /><button type="button" onClick={() => requestDetails('custom', customDates)}>Aplicar</button></div>}
            </section>

            <section className="integration-section">
                <h3>Histórico recente</h3>
                <div className="integration-history">
                    {usage.recent?.map((item, index) => <div key={`${item.timestamp}-${index}`}><time>{formatTime(item.timestamp)}</time><span>{item.request_type}</span><span>Prompt<br /><b>{item.input_tokens == null ? 'Indisponível' : `${formatNumber(item.input_tokens)} tokens`}</b></span><span>Resposta<br /><b>{item.output_tokens == null ? 'Indisponível' : `${formatNumber(item.output_tokens)} tokens`}</b></span><strong>{item.total_tokens == null ? 'Indisponível' : `${formatNumber(item.total_tokens)} total`}</strong></div>)}
                    {!usage.recent?.length && <p>Nenhuma chamada registrada neste período.</p>}
                </div>
            </section>

            <section className="integration-section">
                <h3>Configuração</h3>
                <dl className="integration-config"><div><dt>Modelo atual</dt><dd>{metadata.main_model || 'Indisponível'}</dd></div><div><dt>Modelo Live</dt><dd>{metadata.live_model || 'Indisponível'}</dd></div></dl>
                <div className="integration-actions">
                    <button type="button" onClick={() => socket.emit('test_integration_connection', { integration_id: selectedId })} disabled={current.status === 'checking'}><Zap size={16} />Testar conexão</button>
                    <button type="button" onClick={() => socket.emit('refresh_integration_status', actionPayload)}><RefreshCw size={16} />Atualizar Status</button>
                </div>
            </section>
        </main>
    );
}

export default IntegrationCenter;
