import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FolderInput, FolderPlus, Plus, RefreshCw, X } from 'lucide-react';
import ProjectWorkspaceCard from './ProjectWorkspaceCard';
import ProjectFileBrowser from './ProjectFileBrowser';
import { selectProjectFolder } from './projectFolderPicker.mjs';
import './ProjectWorkspacePage.css';

const API = 'http://localhost:8000/api/project-workspaces';

async function api(path = '', options) {
    const response = await fetch(`${API}${path}`, { headers: { 'Content-Type': 'application/json' }, ...options });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.error?.message || 'Operação não concluída.');
    return payload;
}

export default function ProjectWorkspacePage() {
    const [projects, setProjects] = useState([]);
    const [activeProject, setActiveProject] = useState(null);
    const [relativePath, setRelativePath] = useState('');
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [dialog, setDialog] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const submitInFlight = useRef(false);

    const loadProjects = useCallback(async () => {
        setLoading(true); setError('');
        try { setProjects((await api()).projects); }
        catch (requestError) { setError(requestError.message); }
        finally { setLoading(false); }
    }, []);

    const loadDirectory = useCallback(async (project = activeProject, path = relativePath) => {
        if (!project) return;
        setLoading(true); setError('');
        try {
            const payload = await api(`/${project.id}/directory?path=${encodeURIComponent(path)}`);
            setItems(payload.items); setRelativePath(payload.relative_path); setActiveProject(payload.project);
        } catch (requestError) { setError(requestError.message); }
        finally { setLoading(false); }
    }, [activeProject, relativePath]);

    useEffect(() => { loadProjects(); }, [loadProjects]);

    async function configure(project) {
        try {
            const selected = await selectProjectFolder();
            if (!selected) return;
            await api(`/${project.id}/root`, { method: 'PUT', body: JSON.stringify({ root_path: selected }) });
            await loadProjects();
        } catch (requestError) { setError(requestError.message); }
    }

    async function submitDialog(event) {
        event.preventDefault();
        if (submitInFlight.current) return;
        submitInFlight.current = true;
        setSubmitting(true);
        const form = new FormData(event.currentTarget);
        try {
            if (dialog.type === 'folder') await api(`/${activeProject.id}/folders`, { method: 'POST', body: JSON.stringify({ parent_path: relativePath, name: form.get('name') }) });
            if (dialog.type === 'note') await api(`/${activeProject.id}/text-files`, { method: 'POST', body: JSON.stringify({ parent_path: relativePath, name: form.get('name'), content: form.get('content') }) });
            if (dialog.type === 'rename') await api(`/${activeProject.id}/items`, { method: 'PATCH', body: JSON.stringify({ relative_path: dialog.item.relative_path, name: form.get('name') }) });
            if (dialog.type === 'workspace') {
                const payload = {
                    name: form.get('name'), description: form.get('description'),
                    icon: form.get('icon'), type: form.get('type'),
                    ...(dialog.selectedPath && dialog.locationMode === 'existing'
                        ? { root_path: dialog.selectedPath }
                        : dialog.selectedPath && dialog.locationMode === 'new'
                            ? { parent_path: dialog.selectedPath, folder_name: form.get('folder_name') }
                            : {}),
                };
                await api('', { method: 'POST', body: JSON.stringify(payload) });
            }
            setDialog(null); await loadDirectory();
            if (dialog.type === 'workspace') await loadProjects();
        } catch (requestError) { setError(requestError.message); }
        finally { submitInFlight.current = false; setSubmitting(false); }
    }

    async function selectWorkspaceLocation(mode) {
        try {
            const selected = await selectProjectFolder();
            if (selected) setDialog(current => ({ ...current, locationMode: mode, selectedPath: selected }));
        } catch (requestError) {
            setError(requestError.message);
        }
    }

    async function removeWorkspace(project) {
        if (!window.confirm(`Remover “${project.name}” da Verônica?\n\nA pasta física e todos os arquivos permanecerão intactos.`)) return;
        try { await api(`/${project.id}`, { method: 'DELETE' }); await loadProjects(); }
        catch (requestError) { setError(requestError.message); }
    }

    async function openItem(item, reveal = false) {
        if (item.kind === 'directory' && !reveal) return loadDirectory(activeProject, item.relative_path);
        try { await api(`/${activeProject.id}/open?reveal=${reveal}`, { method: 'POST', body: JSON.stringify({ relative_path: item.relative_path }) }); }
        catch (requestError) { setError(requestError.message); }
    }

    return (
        <main className="project-workspace-page">
            <div className="project-workspace-page__backdrop" />
            {!activeProject ? (
                <section className="project-workspace-home">
                    <header className="project-workspace-title">
                        <div><span>CENTRAL DE ARQUIVOS</span><h1>Projetos</h1><p>Workspaces conectados diretamente ao filesystem.</p></div>
                        <div className="project-workspace-title__actions"><button type="button" className="is-primary" onClick={() => setDialog({ type: 'workspace', selectedPath: null, locationMode: null })}><Plus size={16} /> Novo Projeto</button><button type="button" onClick={loadProjects}><RefreshCw size={16} /> Atualizar</button></div>
                    </header>
                    {error && <div className="project-alert">{error}<button onClick={() => setError('')}><X size={15} /></button></div>}
                    {!loading && <div className="project-list-label">SEUS PROJETOS <span>{projects.length}</span></div>}
                    {loading ? <div className="project-empty">Carregando workspaces…</div> : <div className="project-card-grid">{projects.map(project => <ProjectWorkspaceCard key={project.id} project={project} onOpen={projectToOpen => { setActiveProject(projectToOpen); loadDirectory(projectToOpen, ''); }} />)}</div>}
                </section>
            ) : (
                <>
                    {error && <div className="project-alert project-alert--floating">{error}<button onClick={() => setError('')}><X size={15} /></button></div>}
                    <ProjectFileBrowser project={activeProject} relativePath={relativePath} items={items} loading={loading} onNavigate={path => loadDirectory(activeProject, path)} onHome={() => { setActiveProject(null); setRelativePath(''); setItems([]); loadProjects(); }} onRefresh={() => loadDirectory()} onCreateFolder={() => setDialog({ type: 'folder' })} onCreateNote={() => setDialog({ type: 'note' })} onOpen={item => openItem(item)} onReveal={item => openItem(item, true)} onRename={item => setDialog({ type: 'rename', item })} />
                </>
            )}
            {dialog && <div className="project-dialog-layer" onMouseDown={event => event.target === event.currentTarget && !submitting && setDialog(null)}><form className={`project-dialog ${dialog.type === 'workspace' ? 'project-dialog--wide' : ''}`} onSubmit={submitDialog}><header><div><span>WORKSPACE</span><h2>{dialog.type === 'workspace' ? 'Novo Projeto' : dialog.type === 'folder' ? 'Nova pasta' : dialog.type === 'note' ? 'Nova nota' : 'Renomear item'}</h2></div><button type="button" disabled={submitting} onClick={() => setDialog(null)}><X size={18} /></button></header><label>Nome<input autoFocus name="name" defaultValue={dialog.item?.name || (dialog.type === 'note' ? 'nota.md' : '')} required /></label>{dialog.type === 'workspace' && <><label>Descrição <span className="project-dialog__optional">opcional</span><textarea name="description" rows="3" placeholder="Contexto breve deste workspace" /></label><div className="project-dialog__row"><label>Categoria<select name="type" defaultValue="general"><option value="general">Geral</option><option value="business">Empresa</option><option value="study">Estudos</option><option value="personal">Pessoal</option><option value="cad">CAD</option></select></label><label>Ícone<select name="icon" defaultValue="folder"><option value="folder">Pasta</option><option value="briefcase">Empresa</option><option value="book">Estudos</option><option value="cad">CAD</option><option value="layers">Produto</option></select></label></div><p className="project-dialog__optional">A pasta é opcional e pode ser vinculada depois.</p><div className="project-location-choices"><button type="button" disabled={submitting} className={dialog.locationMode === 'existing' ? 'is-selected' : ''} onClick={() => selectWorkspaceLocation('existing')}><FolderInput size={20} /><span><strong>Vincular pasta existente</strong><small>Nenhum arquivo será movido</small></span></button><button type="button" disabled={submitting} className={dialog.locationMode === 'new' ? 'is-selected' : ''} onClick={() => selectWorkspaceLocation('new')}><FolderPlus size={20} /><span><strong>Criar nova pasta</strong><small>Escolha o local pai</small></span></button></div>{dialog.selectedPath && <div className="project-selected-path">{dialog.locationMode === 'new' ? 'Local pai: ' : 'Pasta: '}{dialog.selectedPath}</div>}{dialog.locationMode === 'new' && <label>Nome da nova pasta<input name="folder_name" required placeholder="Nome válido no Windows" /></label>}</>}{dialog.type === 'note' && <label>Conteúdo<textarea name="content" rows="8" placeholder="Escreva a nota…" /></label>}<footer><button type="button" disabled={submitting} onClick={() => setDialog(null)}>Cancelar</button><button type="submit" disabled={submitting} className="is-primary">{submitting ? 'Criando…' : dialog.type === 'workspace' ? 'Criar projeto' : 'Salvar'}</button></footer></form></div>}
        </main>
    );
}
