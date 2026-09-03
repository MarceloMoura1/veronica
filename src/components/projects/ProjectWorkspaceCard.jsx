import React from 'react';
import { BookOpen, Boxes, Briefcase, ChevronRight, Cpu, Folder, FolderCog, Layers3 } from 'lucide-react';

const icons = { layers: Layers3, boxes: Boxes, cpu: Cpu, cad: FolderCog, book: BookOpen, briefcase: Briefcase, folder: Folder };

const statusCopy = { available: 'Conectado', not_configured: 'Storage opcional', storage_unavailable: 'Storage indisponível' };

export default function ProjectWorkspaceCard({ project, onOpen }) {
    const Icon = icons[project.icon] || Folder;
    const status = statusCopy[project.status] || statusCopy.not_configured;
    return (
        <button type="button" className="project-card" onClick={() => onOpen(project)} aria-label={`Abrir projeto ${project.name}`}>
            <span className="project-card__icon"><Icon size={21} /></span>
            <span className="project-card__identity">
                <strong>{project.name}</strong>
                {project.description && <small>{project.description}</small>}
            </span>
            <span className={`project-card__status project-card__status--${project.status}`}>
                <i aria-hidden="true" />{status}
            </span>
            <span className="project-card__chevron" aria-hidden="true">
                <ChevronRight size={19} />
            </span>
        </button>
    );
}
