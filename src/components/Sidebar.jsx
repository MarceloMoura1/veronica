import React from 'react';
import { Activity, Briefcase, Home, Menu, MonitorSmartphone, PlugZap, Wrench } from 'lucide-react';

const sections = [
    { id: 'home', label: 'Início', icon: Home },
    { id: 'projects', label: 'Projetos', icon: Briefcase },
    { id: 'integrations', label: 'Integrações', icon: PlugZap },
    { id: 'tools', label: 'Ferramentas', icon: Wrench },
    { id: 'devices', label: 'Dispositivos', icon: MonitorSmartphone },
    { id: 'reports', label: 'Relatórios', icon: Activity }
];

export const sectionLabels = Object.fromEntries(sections.map(({ id, label }) => [id, label]));

function Sidebar({ isOpen, activeSection, onToggle, onSectionChange }) {
    return (
        <aside className={`veronica-sidebar ${isOpen ? 'veronica-sidebar--open' : ''}`} aria-label="Navegação principal da Verônica">
            <div className="veronica-sidebar__header">
                <button type="button" className="veronica-sidebar__toggle" onClick={onToggle}
                    aria-label={isOpen ? 'Recolher menu lateral' : 'Expandir menu lateral'}
                    aria-expanded={isOpen} title={isOpen ? 'Recolher menu' : 'Expandir menu'}>
                    <Menu size={21} aria-hidden="true" />
                </button>
                <span className="veronica-sidebar__brand" aria-hidden={!isOpen}>VERÔNICA</span>
            </div>
            <nav className="veronica-sidebar__nav">
                {sections.map(({ id, label, icon: Icon }) => {
                    const isActive = activeSection === id;
                    return (
                        <button key={id} type="button"
                            className={`veronica-sidebar__item ${isActive ? 'veronica-sidebar__item--active' : ''}`}
                            onClick={() => onSectionChange(id)} aria-current={isActive ? 'page' : undefined}
                            aria-label={label} title={!isOpen ? label : undefined}>
                            <Icon size={19} aria-hidden="true" /><span>{label}</span>
                        </button>
                    );
                })}
            </nav>
        </aside>
    );
}

export function SectionPlaceholder({ section }) {
    const label = sectionLabels[section] || 'Seção';
    return (
        <main className="veronica-section-placeholder" aria-labelledby="section-placeholder-title">
            <div className="veronica-section-placeholder__panel">
                <span className="veronica-section-placeholder__eyebrow">MÓDULO VERÔNICA</span>
                <h2 id="section-placeholder-title">{label}</h2>
                <div className="veronica-section-placeholder__line" />
                <p>Em desenvolvimento</p>
            </div>
        </main>
    );
}

export default Sidebar;
