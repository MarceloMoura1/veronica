import React from 'react';
import { ChevronRight, Home } from 'lucide-react';

export default function ProjectBreadcrumb({ project, relativePath, onNavigate, onHome }) {
    const segments = relativePath ? relativePath.split('/') : [];
    return (
        <nav className="project-breadcrumb" aria-label="Caminho do projeto">
            <button type="button" onClick={onHome}><Home size={14} /> Projetos</button>
            <ChevronRight size={14} />
            <button type="button" onClick={() => onNavigate('')}>{project.name}</button>
            {segments.map((segment, index) => (
                <React.Fragment key={`${segment}-${index}`}>
                    <ChevronRight size={14} />
                    <button type="button" onClick={() => onNavigate(segments.slice(0, index + 1).join('/'))}>{segment}</button>
                </React.Fragment>
            ))}
        </nav>
    );
}
