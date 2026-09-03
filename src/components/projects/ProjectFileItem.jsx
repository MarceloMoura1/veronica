import React from 'react';
import { Box, File, FileImage, FileSpreadsheet, FileText, Folder, Link2 } from 'lucide-react';

function itemIcon(item) {
    if (item.kind === 'directory') return Folder;
    if (['.stl', '.step', '.stp', '.iges', '.igs', '.dwg', '.dxf', '.3mf'].includes(item.extension)) return Box;
    if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'].includes(item.extension)) return FileImage;
    if (['.xls', '.xlsx', '.csv'].includes(item.extension)) return FileSpreadsheet;
    if (['.txt', '.md', '.doc', '.docx', '.pdf'].includes(item.extension)) return FileText;
    if (['.url', '.lnk'].includes(item.extension)) return Link2;
    return File;
}

function formatSize(size) {
    if (size === null || size === undefined) return 'Pasta';
    if (size < 1024) return `${size} B`;
    if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 ** 2).toFixed(1)} MB`;
}

export default function ProjectFileItem({ item, onOpen, onReveal, onRename }) {
    const Icon = itemIcon(item);
    return (
        <div className="project-file-row" onDoubleClick={() => onOpen(item)}>
            <span className={`project-file-row__icon ${item.kind === 'directory' ? 'is-folder' : ''}`}><Icon size={20} /></span>
            <div className="project-file-row__name"><strong>{item.name}</strong><span>{item.extension || (item.kind === 'directory' ? 'Diretório' : 'Arquivo')}</span></div>
            <span className="project-file-row__size">{formatSize(item.size)}</span>
            <span className="project-file-row__date">{new Date(item.modified_at).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}</span>
            <div className="project-file-row__actions">
                <button type="button" onClick={() => onOpen(item)}>Abrir</button>
                <button type="button" onClick={() => onRename(item)}>Renomear</button>
                <button type="button" onClick={() => onReveal(item)}>Revelar</button>
            </div>
        </div>
    );
}
