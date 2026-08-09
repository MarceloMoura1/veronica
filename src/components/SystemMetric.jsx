import React from 'react';
import Sparkline from './Sparkline';

export default function SystemMetric({ icon: Icon, label, value, detail, history, tone = 'cyan' }) {
    return (
        <article className={`system-metric system-metric--${tone}`}>
            <div className="system-metric__heading">
                <span className="system-metric__label"><Icon size={13} />{label}</span>
                <strong>{value}</strong>
            </div>
            <Sparkline values={history} label={`${label}, últimos 60 segundos`} />
            <span className="system-metric__detail">{detail}</span>
        </article>
    );
}
