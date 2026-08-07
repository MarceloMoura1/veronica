import React, { useEffect, useMemo, useState } from 'react';
import { Activity, ArrowLeft, Gauge, KeyRound, RefreshCw, ServerCog, X, Zap } from 'lucide-react';
import './IntegrationCenter.css';

const statusLabels = { active: 'Conectado', inactive: 'Inativo', error: 'Erro', checking: 'Verificando', not_configured: 'Não configurado' };
const periods = [['today', 'Hoje'], ['yesterday', 'Ontem'], ['last_7_days', 'Últimos 7 dias'], ['this_month', 'Este mês'], ['custom', 'Personalizado']];
const formatNumber = (value) => Number.isFinite(value) ? value.toLocaleString('pt-BR') : 'Indisponível';
const formatTime = (value) => value ? new Date(value).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : 'Indisponível';
const formatDateTime = (value) => value ? new Date(value).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : 'Indisponível';
const formatModalities = (details) => Array.isArray(details) && details.length ? details.map(({ modality, token_count: count }) => `${modality || 'Não especificada'}: ${count == null ? 'Indisponível' : formatNumber(count)}`).join(' · ') : null;

function Status({ value }) {
    return <span className={`integration-status integration-status--${value || 'inactive'}`}><i />{statusLabels[value] || 'Indisponível'}</span>;
}

function Modal({ title, className = '', onClose, children }) {
    return <div className="integration-modal" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
        <section className={`integration-modal__panel ${className}`} role="dialog" aria-modal="true" aria-label={title}>
            <header><h3>{title}</h3><button type="button" onClick={onClose} aria-label="Fechar"><X size={17} /></button></header>{children}
        </section>
    </div>;
}

function HistoryModal({ usage, period, customDates, setCustomDates, onPeriod, onApply, onClose }) {
    return <Modal title="Histórico de telemetria" className="integration-modal__panel--history" onClose={onClose}>
        <div className="integration-history-controls">
            <div className="integration-periods">{periods.map(([value, label]) => <button key={value} type="button" className={period === value ? 'active' : ''} onClick={() => onPeriod(value)}>{label}</button>)}</div>
            {period === 'custom' && <div className="integration-custom-dates"><input aria-label="Data inicial" type="date" value={customDates.start_date} onChange={(event) => setCustomDates({ ...customDates, start_date: event.target.value })} /><input aria-label="Data final" type="date" value={customDates.end_date} onChange={(event) => setCustomDates({ ...customDates, end_date: event.target.value })} /><button type="button" onClick={onApply}>Aplicar</button></div>}
        </div>
        <div className="integration-history">
            {usage.recent?.map((item, index) => <article key={`${item.timestamp}-${index}`}>
                <time>{formatDateTime(item.timestamp)}</time><span className="integration-history__type">{item.request_type || 'Indisponível'}</span>
                <span>Prompt <b>{item.input_tokens == null ? 'Indisponível' : `${formatNumber(item.input_tokens)} tokens`}</b><small>{formatModalities(item.prompt_tokens_details)}</small></span>
                <span>Saída <b>{(item.visible_output_tokens ?? item.output_tokens) == null ? 'Indisponível' : `${formatNumber(item.visible_output_tokens ?? item.output_tokens)} tokens`}</b><small>{formatModalities(item.output_tokens_details)}</small>{item.thinking_tokens != null && <small>+ {formatNumber(item.thinking_tokens)} raciocínio</small>}</span>
                <strong>{item.total_tokens == null ? 'Indisponível' : `${formatNumber(item.total_tokens)} total`}</strong>
            </article>)}
            {!usage.recent?.length && <p>Nenhuma chamada registrada neste período.</p>}
        </div>
    </Modal>;
}

function IntegrationCenter({ socket }) {
    const [integrations, setIntegrations] = useState([]);
    const [selectedId, setSelectedId] = useState(null);
    const [details, setDetails] = useState(null);
    const [period, setPeriod] = useState('today');
    const [customDates, setCustomDates] = useState({ start_date: '', end_date: '' });
    const [modal, setModal] = useState(null);
    const [apiKey, setApiKey] = useState('');
    const [budget, setBudget] = useState('');
    const [feedback, setFeedback] = useState('');
    const [syncedAt, setSyncedAt] = useState(null);
    const selected = useMemo(() => integrations.find((item) => item.id === selectedId), [integrations, selectedId]);

    const requestDetails = (nextPeriod = period, dates = customDates) => {
        if (selectedId) socket.emit('get_integration_details', { integration_id: selectedId, period: nextPeriod, ...(nextPeriod === 'custom' ? dates : {}) });
    };

    useEffect(() => {
        const markSynced = () => setSyncedAt(new Date().toISOString());
        const onRegistry = ({ integrations: items = [] } = {}) => { setIntegrations(Array.isArray(items) ? items : []); markSynced(); };
        const onDetails = (payload) => { setDetails(payload); markSynced(); };
        const mergeStatus = (payload) => { setDetails((current) => current?.id === payload?.id ? { ...current, ...payload } : current); markSynced(); };
        const onRealtime = ({ details: update } = {}) => { setDetails((current) => current?.id === update?.id ? { ...current, ...update, usage: period === 'today' ? update.usage : current.usage } : current); markSynced(); };
        const onKey = (payload) => { setFeedback(payload?.success ? 'API key atualizada com segurança. Reinicie a sessão Live para aplicá-la.' : (payload?.error || 'Não foi possível atualizar a key.')); if (payload?.success) { setApiKey(''); requestDetails(); } };
        const onError = ({ message } = {}) => setFeedback(message || 'Não foi possível concluir a ação.');
        socket.on('integration_registry', onRegistry); socket.on('integration_details', onDetails); socket.on('integration_test_result', mergeStatus); socket.on('integration_realtime_update', onRealtime); socket.on('integration_key_result', onKey); socket.on('integration_action_error', onError);
        socket.emit('get_integrations');
        return () => { socket.off('integration_registry', onRegistry); socket.off('integration_details', onDetails); socket.off('integration_test_result', mergeStatus); socket.off('integration_realtime_update', onRealtime); socket.off('integration_key_result', onKey); socket.off('integration_action_error', onError); };
    }, [socket, period, selectedId]);

    useEffect(() => { if (selectedId) requestDetails(); }, [selectedId]);
    useEffect(() => { if (!modal) setFeedback(''); }, [modal]);

    if (!selectedId) return <main className="integration-center" aria-labelledby="integrations-title"><header className="integration-center__header"><p>VERÔNICA / INTEGRATION MANAGER</p><h2 id="integrations-title">Integrações</h2></header><div className="integration-list">{integrations.map((integration) => <button key={integration.id} type="button" className="integration-list__item" onClick={() => setSelectedId(integration.id)}><span className="integration-list__icon"><ServerCog size={20} /></span><span className="integration-list__identity"><strong>{integration.name}</strong><small>{integration.provider}</small></span><Status value={integration.status} /></button>)}{!integrations.length && <div className="integration-list__empty">Aguardando IntegrationManager…</div>}</div></main>;

    const current = details || selected || {};
    const usage = current.usage || {};
    const monthly = current.usage_monthly || {};
    const metadata = current.metadata || {};
    const monthlyBudget = current.preferences?.monthly_token_budget;
    const percentage = Number.isFinite(monthly.total_tokens) && Number.isFinite(monthlyBudget) ? (monthly.total_tokens / monthlyBudget) * 100 : null;
    const progress = Math.min(percentage || 0, 100);
    const budgetTone = percentage > 90 ? 'danger' : percentage >= 70 ? 'warning' : 'normal';
    const average = usage.requests > 0 && Number.isFinite(usage.total_tokens) ? usage.total_tokens / usage.requests : null;
    const successRate = usage.requests > 0 && Number.isFinite(usage.integration_errors) ? Math.max(0, ((usage.requests - usage.integration_errors) / usage.requests) * 100) : null;
    const latest = usage.recent?.[0];
    const lastError = usage.recent?.find((item) => item.success === false && !item.diagnostics?.tool_outcome);
    const choosePeriod = (next) => { setPeriod(next); if (next !== 'custom') requestDetails(next); };
    const actionPayload = { integration_id: selectedId, period, ...(period === 'custom' ? customDates : {}) };

    return <main className="integration-center integration-center--detail" aria-labelledby="integration-detail-title">
        <button type="button" className="integration-back" onClick={() => { setSelectedId(null); setDetails(null); }}><ArrowLeft size={15} /> Integrações</button>
        <header className="integration-detail__header"><div><h2 id="integration-detail-title">{current.name}</h2><p>{current.provider}</p></div><div className="integration-detail__state"><Status value={current.status} /><small>última verificação {formatTime(current.last_check)}</small></div></header>

        <div className="integration-dashboard">
            <section className="integration-panel integration-panel--status"><div className="integration-panel__title"><ServerCog size={15} /><h3>Status / Configuração</h3></div><dl className="integration-facts"><div><dt>Status</dt><dd><Status value={current.status} /></dd></div><div><dt>Provider</dt><dd>{current.provider || 'Indisponível'}</dd></div><div><dt>Modelo</dt><dd>{metadata.main_model || 'Indisponível'}</dd></div><div><dt>Modelo Live</dt><dd>{metadata.live_model || 'Indisponível'}</dd></div><div><dt>Latência</dt><dd>{current.latency_ms == null ? 'Indisponível' : `${current.latency_ms} ms`}</dd></div><div><dt>API / SDK</dt><dd>{current.api_key_configured ? 'Configurada' : 'Não configurada'} · {metadata.sdk_version ? `${metadata.sdk_name} ${metadata.sdk_version}` : 'SDK indisponível'}</dd></div></dl><div className="integration-actions"><button type="button" onClick={() => setModal('key')}><KeyRound size={14} />Alterar Key</button><button type="button" onClick={() => socket.emit('test_integration_connection', { integration_id: selectedId })} disabled={current.status === 'checking'}><Zap size={14} />Testar Conexão</button><button type="button" onClick={() => socket.emit('refresh_integration_status', actionPayload)}><RefreshCw size={14} />Atualizar Status</button></div>{current.last_error && <p className="integration-error">{current.last_error}</p>}</section>

            <section className="integration-panel integration-panel--budget"><div className="integration-panel__title"><Gauge size={15} /><h3>Consumo mensal</h3><strong>{percentage == null ? '—' : `${percentage.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`}</strong></div>{monthlyBudget ? <><div className={`integration-budget integration-budget--${budgetTone}`}><div style={{ width: `${progress}%` }} /></div><div className="integration-budget__caption"><span>{formatNumber(monthly.total_tokens)} / {formatNumber(monthlyBudget)} tokens</span>{percentage > 100 && <b>Meta excedida</b>}</div><button className="integration-text-action" type="button" onClick={() => { setBudget(String(monthlyBudget)); setModal('budget'); }}>Editar meta</button></> : <div className="integration-empty"><span>Meta mensal não definida</span><button type="button" onClick={() => setModal('budget')}>Definir meta</button></div>}<small className="integration-note">Meta configurada por você · consumo real do mês calendário</small></section>

            <section className="integration-panel integration-panel--health"><div className="integration-panel__title"><Activity size={15} /><h3>Saúde</h3></div><div className="integration-health"><div><span>Taxa de sucesso</span><strong>{successRate == null ? 'Indisponível' : `${successRate.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`}</strong></div><div><span>Retries</span><strong>{formatNumber(usage.retries)}</strong></div><div><span>Erros Gemini</span><strong>{formatNumber(usage.integration_errors)}</strong></div></div></section>

            <section className="integration-panel integration-panel--telemetry"><div className="integration-panel__title"><h3>Telemetria · {periods.find(([value]) => value === period)?.[1]}</h3></div><div className="integration-metrics"><div><span>Entrada</span><strong>{formatNumber(usage.input_tokens)}</strong></div><div><span>Saída</span><strong>{formatNumber(usage.visible_output_tokens ?? usage.output_tokens)}</strong></div><div><span>Raciocínio</span><strong>{formatNumber(usage.thinking_tokens)}</strong></div><div><span>Total</span><strong>{formatNumber(usage.total_tokens)}</strong></div><div><span>Requisições</span><strong>{formatNumber(usage.requests)}</strong></div><div><span>Tokens / requisição</span><strong>{average == null ? 'Indisponível' : formatNumber(Math.round(average))}</strong></div></div></section>

            <section className="integration-panel integration-panel--activity"><div className="integration-panel__title"><h3>Atividade</h3></div><div className="integration-activity"><div><span>Última chamada</span><strong>{formatTime(latest?.timestamp)}</strong></div><div><span>Último erro</span><strong>{formatTime(lastError?.timestamp)}</strong></div><div><span>Última sincronização</span><strong>{formatTime(syncedAt)}</strong></div><button type="button" onClick={() => setModal('history')}>Ver histórico →</button></div></section>
        </div>

        {modal === 'history' && <HistoryModal usage={usage} period={period} customDates={customDates} setCustomDates={setCustomDates} onPeriod={choosePeriod} onApply={() => requestDetails('custom', customDates)} onClose={() => setModal(null)} />}
        {modal === 'key' && <Modal title="Alterar API key" onClose={() => { setModal(null); setApiKey(''); }}><form className="integration-form" onSubmit={(event) => { event.preventDefault(); setFeedback('Salvando…'); socket.emit('update_gemini_api_key', { api_key: apiKey }); }}><label>Nova API key<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Cole a nova key" /></label><p>A key não é exibida, recuperada nem armazenada nesta interface.</p>{feedback && <output>{feedback}</output>}<button type="submit" disabled={!apiKey.trim()}>Salvar key</button></form></Modal>}
        {modal === 'budget' && <Modal title="Meta mensal de tokens" onClose={() => setModal(null)}><form className="integration-form" onSubmit={(event) => { event.preventDefault(); const parsed = Number(budget); if (!Number.isSafeInteger(parsed) || parsed <= 0) { setFeedback('Informe um inteiro positivo válido.'); return; } socket.emit('update_integration_budget', { integration_id: selectedId, monthly_token_budget: parsed }); setModal(null); }}><label>Orçamento de tokens<input type="number" min="1" step="1" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="Ex.: 1000000" /></label><p>Esta é uma meta pessoal, não um limite oficial do Gemini.</p>{feedback && <output>{feedback}</output>}<button type="submit">Salvar meta</button></form></Modal>}
    </main>;
}

export default IntegrationCenter;
