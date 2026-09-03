import React from 'react';
import { ArrowLeft, FilePlus2, FolderPlus, RefreshCw } from 'lucide-react';
import ProjectBreadcrumb from './ProjectBreadcrumb';
import ProjectFileItem from './ProjectFileItem';

export default function ProjectFileBrowser({ project, relativePath, items, loading, onNavigate, onHome, onRefresh, onCreateFolder, onCreateNote, onOpen, onReveal, onRename }) {
    const parent = relativePath.split('/').slice(0, -1).join('/');
    const folders = items.filter(item => item.kind === 'directory');
    const files = items.filter(item => item.kind === 'file');
    return (
        <section className="project-browser">
            <header className="project-browser__header">
                <ProjectBreadcrumb project={project} relativePath={relativePath} onNavigate={onNavigate} onHome={onHome} />
                <div className="project-browser__toolbar">
                    {relativePath && <button type="button" onClick={() => onNavigate(parent)}><ArrowLeft size={16} /> Voltar</button>}
                    <button type="button" onClick={onRefresh}><RefreshCw size={16} /> Atualizar</button>
                    <button type="button" onClick={onCreateFolder}><FolderPlus size={16} /> Nova pasta</button>
                    <button type="button" className="is-primary" onClick={onCreateNote}><FilePlus2 size={16} /> Nova nota</button>
                </div>
            </header>
            <div className="project-browser__body">
                {loading ? <div className="project-empty">Lendo filesystem…</div> : items.length === 0 ? (
                    <div className="project-empty"><FolderPlus size={34} /><strong>Esta pasta está vazia</strong><span>Arquivos adicionados pelo Windows aparecerão no próximo refresh.</span></div>
                ) : (
                    <>
                        {folders.length > 0 && <div className="project-file-group"><h3>Pastas <span>{folders.length}</span></h3>{folders.map(item => <ProjectFileItem key={item.relative_path} item={item} onOpen={onOpen} onReveal={onReveal} onRename={onRename} />)}</div>}
                        {files.length > 0 && <div className="project-file-group"><h3>Arquivos <span>{files.length}</span></h3>{files.map(item => <ProjectFileItem key={item.relative_path} item={item} onOpen={onOpen} onReveal={onReveal} onRename={onRename} />)}</div>}
                    </>
                )}
            </div>
        </section>
    );
}
