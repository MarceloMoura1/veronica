import React from 'react';

export default function Sparkline({ values = [], label = 'Histórico recente' }) {
    const width = 116;
    const height = 32;
    const finite = values.filter(Number.isFinite);
    if (finite.length < 2) {
        return <div className="system-sparkline system-sparkline--empty" aria-label={`${label}: aguardando dados`} />;
    }
    const maximum = Math.max(...finite, 1);
    const minimum = Math.min(...finite, 0);
    const span = Math.max(maximum - minimum, 1);
    const points = finite.map((value, index) => {
        const x = (index / (finite.length - 1)) * width;
        const y = height - 2 - ((value - minimum) / span) * (height - 4);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return (
        <svg className="system-sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} preserveAspectRatio="none">
            <polyline points={points} fill="none" vectorEffect="non-scaling-stroke" />
        </svg>
    );
}
