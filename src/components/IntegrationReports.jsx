import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Search, X } from 'lucide-react';
import './IntegrationCenter.css';
import './IntegrationReports.css';

const LABEL = { grave: 'Grave', medio: 'Médio', leve: 'Leve', novo: 'Novo', em_analise: 'Em análise', correcao_proposta: 'Correção proposta', em_correcao: 'Em correção', monitorando: 'Monitorando', reaberto: 'Reaberto', resolvido: 'Resolvido' };
const when = (value) => value ? new Date(value).toLocaleString('pt-BR') : 'Indisponível';

export default function IntegrationReports({ socket }) {
    const [payload, setPayload] = useState({ incidents: [], counts: {} });
    const [severity, setSeverity] = useState('todos');
    const [status, setStatus] = useState('abertos');
    const [period, setPeriod] = useState('30d');
    const [detail, setDetail] = useState(null);
    const [askState, setAskState] = useState({});
    const pendingAsk = useRef(new Set());
    useEffect(() => {
        const receive = (next) => setPayload(next || { incidents: [], counts: {} });
        const receiveDetail = (next) => setDetail(next?.incident || null);
        socket.on('system_incidents', receive); socket.on('incident_details', receiveDetail);
        return () => { socket.off('system_incidents', receive); socket.off('incident_details', receiveDetail); };
    }, [socket]);
    useEffect(() => {
        const refresh = () => socket.emit('list_system_incidents', { severity, status, period });
        refresh(); const timer = window.setInterval(refresh, 5000);
        return () => window.clearInterval(timer);
    }, [socket, severity, status, period]);
    const incidents = useMemo(() => payload.incidents || [], [payload]);
    const ask = (id) => {
        if (pendingAsk.current.has(id)) return;
        pendingAsk.current.add(id);
        setAskState(current => ({ ...current, [id]: 'sending' }));
        socket.timeout(6000).emit('ask_veronica_about_incident', { incident_id: id }, (error, ack) => {
            pendingAsk.current.delete(id);
            const next = !error && ack?.accepted ? 'success' : 'error';
            setAskState(current => ({ ...current, [id]: next }));
            if (next === 'success') window.setTimeout(() => setAskState(current => ({ ...current, [id]: 'idle' })), 2400);
        });
    };
    const askButton = (id) => {
        const state = askState[id] || 'idle';
        const text = { idle: 'Perguntar à Verônica', sending: 'Enviando...', success: '✓ Enviado à Verônica', error: '! Não foi possível enviar' }[state];
        return <button className={`incident-ask incident-ask--${state}`} disabled={state === 'sending'} aria-busy={state === 'sending'} onClick={() => ask(id)}>{text}</button>;
    };
    return <main className="incident-center" aria-labelledby="reports-title">
        <header className="incident-header"><p>VERÔNICA / INCIDENT INTELLIGENCE</p><h2 id="reports-title">Relatórios</h2><span>Fonte única operacional</span></header>
        <section className="incident-counts" aria-label="Incidentes abertos">
            {['grave', 'medio', 'leve'].map(level => <button key={level} className={`incident-count incident-count--${level}`} onClick={() => setSeverity(level)}><span>{LABEL[level]}s</span><strong>{payload.counts?.[level] || 0}</strong></button>)}
        </section>
        <nav className="incident-filters" aria-label="Filtros">
            <div>{['todos', 'grave', 'medio', 'leve'].map(x => <button className={severity === x ? 'active' : ''} onClick={() => setSeverity(x)} key={x}>{x === 'todos' ? 'Todos' : LABEL[x]}</button>)}</div>
            <select value={status} onChange={e => setStatus(e.target.value)}><option value="abertos">Abertos</option><option value="monitorando">Monitorando</option><option value="resolvido">Resolvidos</option><option value="todos">Todos os status</option></select>
            <select value={period} onChange={e => setPeriod(e.target.value)}><option value="today">Hoje</option><option value="7d">7 dias</option><option value="30d">30 dias</option><option value="">Todo período</option></select>
        </nav>
        <section className="incident-list">
            {!incidents.length && <div className="incident-empty"><Search size={22}/><strong>Nenhum incidente encontrado</strong><span>Eventos normais e confirmações negadas não aparecem aqui.</span></div>}
            {incidents.map(item => <article className={`incident-item incident-item--${item.severity}`} key={item.incident_id}>
                <i><AlertTriangle size={17}/></i><div className="incident-main"><span>{LABEL[item.severity]}</span><h3>{item.title}</h3><small>{item.source} / {item.component}</small></div>
                <dl><div><dt>Status</dt><dd>{LABEL[item.status] || item.status}</dd></div><div><dt>Ocorrências</dt><dd>{item.occurrence_count}</dd></div><div><dt>Última ocorrência</dt><dd>{when(item.last_seen)}</dd></div></dl>
                <div className="incident-actions"><button onClick={() => socket.emit('get_incident_details', { incident_id: item.incident_id })}>Detalhes</button>{askButton(item.incident_id)}</div>
            </article>)}
        </section>
        {detail && <div className="incident-modal" role="dialog" aria-modal="true"><section><button className="incident-close" onClick={() => setDetail(null)} aria-label="Fechar"><X/></button><p>{LABEL[detail.severity]} · {LABEL[detail.status] || detail.status}</p><h2>{detail.title}</h2><dl>{[['Fonte', detail.source], ['Componente', detail.component], ['Código', detail.error_code], ['Primeira ocorrência', when(detail.first_seen)], ['Última ocorrência', when(detail.last_seen)], ['Ocorrências', detail.occurrence_count], ['Resumo seguro', detail.safe_summary], ['Diagnóstico', detail.diagnosis || 'Ainda não analisado'], ['Resolução', detail.resolution_summary || 'Não resolvido']].map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>{askButton(detail.incident_id)}</section></div>}
    </main>;
}
