import sys
import asyncio

# Fix for asyncio subprocess support on Windows
# MUST BE SET BEFORE OTHER IMPORTS
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import threading
import sys
import os
import json
import uuid
import platform
from datetime import datetime, timezone
from pathlib import Path



# Ensure we can import ada
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import ada
from authenticator import FaceAuthenticator
from kasa_agent import KasaAgent
from memory import ConversationContextBuilder, ConversationalMemoryAnalyzer, PersonalMemoryManager
from integrations import IntegrationManager
from chat_history import ChatHistoryStore
from system_monitor import SystemMonitor, SystemMonitorTask
from project_workspace import ProjectWorkspaceError, ProjectWorkspaceService

# Create a Socket.IO server
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "null"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app_socketio = socketio.ASGIApp(sio, app)


async def emit_integration_registry(payload):
    await sio.emit("integration_registry", payload)
    for integration in payload.get("integrations", []):
        integration_id = integration.get("id")
        if not integration_id:
            continue
        await sio.emit(
            "integration_realtime_update",
            {
                "details": integration_manager.get_details(integration_id),
                "reports": integration_manager.get_reports(integration_id),
            },
        )


integration_manager = IntegrationManager(event_callback=emit_integration_registry)
chat_history = ChatHistoryStore(Path(__file__).resolve().parent.parent / "data" / "conversations" / "default.jsonl")
project_workspaces = ProjectWorkspaceService()


class ProjectRootRequest(BaseModel):
    root_path: str


class ProjectFolderRequest(BaseModel):
    parent_path: str = ""
    name: str


class ProjectTextFileRequest(BaseModel):
    parent_path: str = ""
    name: str
    content: str


class ProjectItemRequest(BaseModel):
    relative_path: str = ""


class ProjectRenameRequest(BaseModel):
    relative_path: str
    name: str


class ProjectWorkspaceCreateRequest(BaseModel):
    name: str
    root_path: str | None = None
    parent_path: str | None = None
    folder_name: str | None = None
    description: str = ""
    icon: str = "folder"
    type: str = "general"


def project_error_response(error: ProjectWorkspaceError):
    return JSONResponse(status_code=error.status, content=error.payload())


async def emit_system_status(payload):
    await sio.emit("system_status", payload)


system_monitor = SystemMonitor()
system_monitor_task = SystemMonitorTask(system_monitor, emit_system_status, interval=1.0)

import signal

# --- SHUTDOWN HANDLER ---
def signal_handler(sig, frame):
    print(f"\n[SERVER] Caught signal {sig}. Exiting gracefully...")
    integration_manager.shutdown()
    # Clean up audio loop
    if audio_loop:
        try:
            print("[SERVER] Stopping Audio Loop...")
            audio_loop.stop() 
        except:
            pass
    # Force kill
    print("[SERVER] Force exiting...")
    os._exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Global state
audio_loop = None
loop_task = None
printer_monitor_task = None
authenticator = None
kasa_agent = KasaAgent()
SETTINGS_FILE = "settings.json"
personal_memory = None
conversation_context = None
conversational_memory = None


def initialize_runtime_memory(storage_dir=None):
    """Initialize mutable conversation state only during real startup or with injected test storage."""
    global personal_memory, conversation_context, conversational_memory
    personal_memory = PersonalMemoryManager(storage_dir)
    conversation_context = ConversationContextBuilder(personal_memory)
    conversational_memory = ConversationalMemoryAnalyzer(personal_memory, conversation_context)
    return personal_memory, conversation_context, conversational_memory

DEFAULT_SETTINGS = {
    "face_auth_enabled": False, # Default OFF as requested
    "tool_permissions": {
        "generate_cad": True,
        "run_web_agent": True,
        "write_file": True,
        "read_directory": True,
        "read_file": True,
        "create_project": True,
        "switch_project": True,
        "list_projects": True
    },
    "printers": [], # List of {host, port, name, type}
    "kasa_devices": [], # List of {ip, alias, model}
    "camera_flipped": False, # Invert cursor horizontal direction
    "output_device_name": None,
    "speaker_labels": [],
}

SETTINGS = DEFAULT_SETTINGS.copy()

def load_settings():
    global SETTINGS
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults to ensure new keys exist
                # Deep merge for tool_permissions would be better but shallow merge of top keys + tool_permissions check is okay for now
                for k, v in loaded.items():
                    if k in {"audio_output_id", "audio_output_name", "audio_output_aliases"}:
                        continue
                    if k == "tool_permissions" and isinstance(v, dict):
                         SETTINGS["tool_permissions"].update(v)
                    else:
                        SETTINGS[k] = v
            print(f"Loaded settings: {SETTINGS}")
        except Exception as e:
            print(f"Error loading settings: {e}")

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(SETTINGS, f, indent=4)
        print("Settings saved.")
    except Exception as e:
        print(f"Error saving settings: {e}")

# Load on startup
load_settings()


authenticator = None
kasa_agent = KasaAgent(known_devices=SETTINGS.get("kasa_devices"))
# tool_permissions is now SETTINGS["tool_permissions"]

@app.on_event("startup")
async def startup_event():
    import sys
    print(f"[SERVER DEBUG] Startup Event Triggered")
    print(f"[SERVER DEBUG] Python Version: {sys.version}")
    print(f"[SERVER DEBUG] Python Executable: {sys.executable}", flush=True)
    try:
        loop = asyncio.get_running_loop()
        print(f"[SERVER DEBUG] Running Loop: {type(loop)}")
        policy = asyncio.get_event_loop_policy()
        print(f"[SERVER DEBUG] Current Policy: {type(policy)}")
    except Exception as e:
        print(f"[SERVER DEBUG] Error checking loop: {e}")

    if personal_memory is None:
        initialize_runtime_memory()
    print("[SERVER] Startup: Initializing Kasa Agent...")
    await kasa_agent.initialize()
    await integration_manager.test_connection("gemini")
    system_monitor_task.start()


@app.on_event("shutdown")
async def shutdown_event():
    await system_monitor_task.stop()

@app.get("/status")
async def status():
    return {
        "status": "running",
        "service": "A.D.A Backend",
        "backend_version": "1.0",
        "instance_id": os.getenv("ADA_BACKEND_INSTANCE_ID"),
        "pid": os.getpid(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
    }


@app.get("/api/project-workspaces")
async def list_project_workspaces():
    try:
        return {"ok": True, "projects": await asyncio.to_thread(project_workspaces.list_projects)}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.post("/api/project-workspaces")
async def create_project_workspace(request: ProjectWorkspaceCreateRequest):
    try:
        project = await asyncio.to_thread(
            project_workspaces.create_workspace,
            request.name,
            root_path=request.root_path,
            parent_path=request.parent_path,
            folder_name=request.folder_name,
            description=request.description,
            icon=request.icon,
            project_type=request.type,
        )
        return {"ok": True, "project": project}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.delete("/api/project-workspaces/{project_id}")
async def remove_project_workspace(project_id: str):
    try:
        result = await asyncio.to_thread(project_workspaces.remove_workspace, project_id)
        return {"ok": True, "result": result}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.put("/api/project-workspaces/{project_id}/root")
async def configure_project_root(project_id: str, request: ProjectRootRequest):
    try:
        project = await asyncio.to_thread(project_workspaces.configure_root, project_id, request.root_path)
        return {"ok": True, "project": project}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.get("/api/project-workspaces/{project_id}/directory")
async def list_project_directory(project_id: str, path: str = ""):
    try:
        directory = await asyncio.to_thread(project_workspaces.list_directory, project_id, path)
        return {"ok": True, **directory}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.post("/api/project-workspaces/{project_id}/folders")
async def create_project_folder(project_id: str, request: ProjectFolderRequest):
    try:
        item = await asyncio.to_thread(
            project_workspaces.create_folder, project_id, request.parent_path, request.name
        )
        return {"ok": True, "item": item}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.post("/api/project-workspaces/{project_id}/text-files")
async def create_project_text_file(project_id: str, request: ProjectTextFileRequest):
    try:
        item = await asyncio.to_thread(
            project_workspaces.save_text_file,
            project_id,
            request.parent_path,
            request.name,
            request.content,
        )
        return {"ok": True, "item": item}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


@app.patch("/api/project-workspaces/{project_id}/items")
async def rename_project_item(project_id: str, request: ProjectRenameRequest):
    try:
        item = await asyncio.to_thread(
            project_workspaces.rename_item, project_id, request.relative_path, request.name
        )
        return {"ok": True, "item": item}
    except ProjectWorkspaceError as error:
        return project_error_response(error)


def _open_workspace_item(target: str, reveal: bool) -> None:
    import subprocess

    if sys.platform == "win32":
        if reveal and Path(target).is_file():
            subprocess.Popen(["explorer.exe", "/select,", target])
        elif reveal:
            subprocess.Popen(["explorer.exe", target])
        else:
            os.startfile(target)
        return
    subprocess.Popen(["xdg-open", str(Path(target).parent if reveal and Path(target).is_file() else target)])


@app.post("/api/project-workspaces/{project_id}/open")
async def open_project_item(project_id: str, request: ProjectItemRequest, reveal: bool = False):
    try:
        target = await asyncio.to_thread(project_workspaces.get_open_target, project_id, request.relative_path)
        await asyncio.to_thread(_open_workspace_item, target, reveal)
        return {"ok": True}
    except ProjectWorkspaceError as error:
        return project_error_response(error)
    except PermissionError:
        return project_error_response(ProjectWorkspaceError("permission_denied", "Permissão negada.", 403))
    except OSError:
        return project_error_response(ProjectWorkspaceError("io_error", "Não foi possível abrir o item.", 500))

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")
    await sio.emit('status', {'msg': 'Connected to A.D.A Backend'}, room=sid)
    await sio.emit(
        "integration_registry",
        {"integrations": integration_manager.list_integrations()},
        room=sid,
    )
    if system_monitor_task.latest is not None:
        await sio.emit("system_status", system_monitor_task.latest, room=sid)
    global authenticator
    
    # Callback for Auth Status
    async def on_auth_status(is_auth):
        print(f"[SERVER] Auth status change: {is_auth}")
        await sio.emit('auth_status', {'authenticated': is_auth})

    # Callback for Auth Camera Frames
    async def on_auth_frame(frame_b64):
        await sio.emit('auth_frame', {'image': frame_b64})

    # Initialize Authenticator if not already done
    if authenticator is None:
        authenticator = FaceAuthenticator(
            reference_image_path="reference.jpg",
            on_status_change=on_auth_status,
            on_frame=on_auth_frame
        )
    
    # Check if already authenticated or needs to start
    if authenticator.authenticated:
        await sio.emit('auth_status', {'authenticated': True})
    else:
        # Check Settings for Auth
        if SETTINGS.get("face_auth_enabled", False):
            await sio.emit('auth_status', {'authenticated': False})
            # Start the auth loop in background
            asyncio.create_task(authenticator.start_authentication_loop())
        else:
            # Bypass Auth
            print("Face Auth Disabled. Auto-authenticating.")
            # We don't change authenticator state to true to avoid confusion if re-enabled? 
            # Or we should just tell client it's auth'd.
            await sio.emit('auth_status', {'authenticated': True})

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")


@sio.event
async def start_audio(sid, data=None):
    global audio_loop, loop_task
    
    # Optional: Block if not authenticated
    # Only block if auth is ENABLED and not authenticated
    if SETTINGS.get("face_auth_enabled", False):
        if authenticator and not authenticator.authenticated:
            print("Blocked start_audio: Not authenticated.")
            await sio.emit('error', {'msg': 'Authentication Required'})
            return

    print(f"[VOICE_SERVER] start_audio received sid={sid}")
    
    device_index = None
    device_name = None
    output_device_name = None
    if data:
        if 'device_index' in data:
            device_index = data['device_index']
        if 'device_name' in data:
            device_name = data['device_name']
        if 'output_device_name' in data:
            output_device_name = data['output_device_name']

    if not output_device_name:
        output_device_name = SETTINGS.get("output_device_name")
        print(
            "[VOICE_SERVER] start_audio missing output label; "
            f"using persisted output_device_name={output_device_name!r}"
        )
            
    print(
        f"[VOICE_SERVER] device_name={device_name!r} browser_index={device_index!r} "
        f"output_device_name={output_device_name!r} muted={bool(data and data.get('muted'))}"
    )
    
    if audio_loop:
        if loop_task and (loop_task.done() or loop_task.cancelled()):
             print("Audio loop task appeared finished/cancelled. Clearing and restarting...")
             audio_loop = None
             loop_task = None
        else:
             print("Audio loop already running. Re-connecting client to session.")
             await sio.emit('status', {'msg': 'A.D.A Already Running'})
             return


    # Callback to send audio data to frontend
    def on_audio_data(data_bytes):
        # We need to schedule this on the event loop
        # This is high frequency, so we might want to downsample or batch if it's too much
        asyncio.create_task(sio.emit('audio_data', {'data': list(data_bytes)}))

    # Callback to send CAL data to frontend
    def on_cad_data(data):
        info = f"{len(data.get('vertices', []))} vertices" if 'vertices' in data else f"{len(data.get('data', ''))} bytes (STL)"
        print(f"Sending CAD data to frontend: {info}")
        asyncio.create_task(sio.emit('cad_data', data))

    # Callback to send Browser data to frontend
    def on_web_data(data):
        print(f"Sending Browser data to frontend: {len(data.get('log', ''))} chars logs")
        asyncio.create_task(sio.emit('browser_frame', data))
        
    # Callback to send Transcription data to frontend
    transcription_lock = asyncio.Lock()

    def on_transcription(data):
        async def publish():
            async with transcription_lock:
                payload = dict(data)
                if payload.get("final"):
                    source = "voice" if payload["role"] == "user" else "assistant"
                    message, _ = await asyncio.to_thread(chat_history.append, {
                        "id": payload["message_id"], "role": payload["role"],
                        "content": payload["text"], "timestamp": payload["timestamp"], "source": source,
                    })
                    payload["message"] = message
                await sio.emit('transcription', payload)
        asyncio.create_task(publish())

    # Callback to send Confirmation Request to frontend
    def on_tool_confirmation(data):
        # data = {"id": "uuid", "tool": "tool_name", "args": {...}}
        print(f"Requesting confirmation for tool: {data.get('tool')}")
        asyncio.create_task(sio.emit('tool_confirmation_request', data))

    # Callback to send CAD status to frontend
    def on_cad_status(status):
        # status can be: 
        # - a string like "generating" (from ada.py handle_cad_request)
        # - a dict with {status, attempt, max_attempts, error} (from CadAgent)
        if isinstance(status, dict):
            print(f"Sending CAD Status: {status.get('status')} (attempt {status.get('attempt')}/{status.get('max_attempts')})")
            asyncio.create_task(sio.emit('cad_status', status))
        else:
            # Legacy: simple string
            print(f"Sending CAD Status: {status}")
            asyncio.create_task(sio.emit('cad_status', {'status': status}))

    # Callback to send CAD thoughts to frontend (streaming)
    def on_cad_thought(thought_text):
        asyncio.create_task(sio.emit('cad_thought', {'text': thought_text}))

    # Callback to send Project Update to frontend
    def on_project_update(project_name):
        print(f"Sending Project Update: {project_name}")
        asyncio.create_task(sio.emit('project_update', {'project': project_name}))

    # Callback to send Device Update to frontend
    def on_device_update(devices):
        # devices is a list of dicts
        print(f"Sending Kasa Device Update: {len(devices)} devices")
        asyncio.create_task(sio.emit('kasa_devices', devices))

    # Callback to send Error to frontend
    def on_error(msg):
        print(f"Sending Error to frontend: {msg}")
        asyncio.create_task(sio.emit('error', {'msg': msg}))

    # Initialize ADA
    try:
        print("[VOICE_LOOP] creating AudioLoop")
        audio_loop = ada.AudioLoop(
            video_mode="none", 
            on_audio_data=on_audio_data,
            on_cad_data=on_cad_data,
            on_web_data=on_web_data,
            on_transcription=on_transcription,
            on_tool_confirmation=on_tool_confirmation,
            on_cad_status=on_cad_status,
            on_cad_thought=on_cad_thought,
            on_project_update=on_project_update,
            on_device_update=on_device_update,
            on_error=on_error,

            input_device_index=device_index,
            input_device_name=device_name,
            output_device_name=output_device_name,
            conversation_context_builder=conversation_context,
            conversational_memory_analyzer=conversational_memory,
            kasa_agent=kasa_agent,
            integration_manager=integration_manager,
        )
        print("[VOICE_LOOP] AudioLoop created")

        # Apply current permissions
        audio_loop.update_permissions(SETTINGS["tool_permissions"])
        
        # Check initial mute state
        if data and data.get('muted', False):
            print("Starting with Audio Paused")
            audio_loop.set_paused(True)

        print("Creating asyncio task for AudioLoop.run()")
        loop_task = asyncio.create_task(audio_loop.run())
        
        # Add a done callback to catch silent failures in the loop
        def handle_loop_exit(task):
            try:
                task.result()
            except asyncio.CancelledError:
                print("Audio Loop Cancelled")
            except Exception as e:
                print(f"Audio Loop Crashed: {e}")
                # You could emit 'error' here if you have context
        
        loop_task.add_done_callback(handle_loop_exit)
        
        print("Emitting 'A.D.A Started'")
        await sio.emit('status', {'msg': 'A.D.A Started'})

        # Load saved printers
        saved_printers = SETTINGS.get("printers", [])
        if saved_printers and audio_loop.printer_agent:
            print(f"[SERVER] Loading {len(saved_printers)} saved printers...")
            for p in saved_printers:
                audio_loop.printer_agent.add_printer_manually(
                    name=p.get("name", p["host"]),
                    host=p["host"],
                    port=p.get("port", 80),
                    printer_type=p.get("type", "moonraker"),
                    camera_url=p.get("camera_url")
                )
        
        # Start Printer Monitor
        asyncio.create_task(monitor_printers_loop())
        
    except Exception as e:
        print(f"CRITICAL ERROR STARTING ADA: {e}")
        import traceback
        traceback.print_exc()
        await sio.emit('error', {'msg': f"Failed to start: {str(e)}"})
        audio_loop = None # Ensure we can try again


async def monitor_printers_loop():
    """Background task to query printer status periodically."""
    print("[SERVER] Starting Printer Monitor Loop")
    while audio_loop and audio_loop.printer_agent:
        try:
            agent = audio_loop.printer_agent
            if not agent.printers:
                await asyncio.sleep(5)
                continue
                
            tasks = []
            for host, printer in agent.printers.items():
                if printer.printer_type.value != "unknown":
                    tasks.append(agent.get_print_status(host))
            
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        pass # Ignore errors for now
                    elif res:
                        # res is PrintStatus object
                        await sio.emit('print_status_update', res.to_dict())
                        
        except asyncio.CancelledError:
            print("[SERVER] Printer Monitor Cancelled")
            break
        except Exception as e:
            print(f"[SERVER] Monitor Loop Error: {e}")
            
        await asyncio.sleep(2) # Update every 2 seconds for responsiveness

async def _stop_voice_session(*, emit_status=True):
    global audio_loop, loop_task
    stopping_loop, stopping_task = audio_loop, loop_task
    if not stopping_loop and not stopping_task:
        return
    if stopping_loop:
        stopping_loop.stop()
    if stopping_task and not stopping_task.done():
        stopping_task.cancel()
    if stopping_task:
        await asyncio.gather(stopping_task, return_exceptions=True)
    if audio_loop is stopping_loop:
        audio_loop = None
    if loop_task is stopping_task:
        loop_task = None
    if emit_status:
        await sio.emit('status', {'msg': 'A.D.A Stopped'})


@sio.event
async def stop_audio(sid):
    global audio_loop
    if audio_loop:
        audio_loop.stop() 
        print("Stopping Audio Loop")
        audio_loop = None
        await sio.emit('status', {'msg': 'A.D.A Stopped'})

@sio.event
async def pause_audio(sid):
    global audio_loop
    if audio_loop:
        audio_loop.set_paused(True)
        print("Pausing Audio")
        await sio.emit('status', {'msg': 'Audio Paused'})

@sio.event
async def resume_audio(sid):
    global audio_loop
    if audio_loop:
        audio_loop.set_paused(False)
        print("Resuming Audio")
        await sio.emit('status', {'msg': 'Audio Resumed'})

@sio.event
async def confirm_tool(sid, data):
    # data: { "id": "...", "confirmed": True/False }
    request_id = data.get('id')
    confirmed = data.get('confirmed', False)
    
    print(f"[SERVER DEBUG] Received confirmation response for {request_id}: {confirmed}")
    
    if audio_loop:
        audio_loop.resolve_tool_confirmation(request_id, confirmed)
    else:
        print("Audio loop not active, cannot resolve confirmation.")

@sio.event
async def shutdown(sid, data=None):
    """Gracefully shutdown the server when the application closes."""
    global audio_loop, loop_task, authenticator
    
    print("[SERVER] ========================================")
    print("[SERVER] SHUTDOWN SIGNAL RECEIVED FROM FRONTEND")
    print("[SERVER] ========================================")
    
    # Stop audio loop
    if audio_loop:
        print("[SERVER] Stopping Audio Loop...")
        audio_loop.stop()
        audio_loop = None
    
    # Cancel the loop task if running
    if loop_task and not loop_task.done():
        print("[SERVER] Cancelling loop task...")
        loop_task.cancel()
        loop_task = None
    
    # Stop authenticator if running
    if authenticator:
        print("[SERVER] Stopping Authenticator...")
        authenticator.stop()
    
    print("[SERVER] Graceful shutdown complete. Terminating process...")
    
    # Force exit immediately - os._exit bypasses cleanup but ensures termination
    os._exit(0)

@sio.event
async def user_input(sid, data):
    text = data.get('text')
    print(f"[SERVER DEBUG] User input received: '{text}'")

    if not isinstance(text, str) or not text.strip():
        return {"accepted": False, "reason": "empty_text"}

    message, _ = await asyncio.to_thread(chat_history.append, {
        "id": data.get("message_id"), "role": "user", "content": text,
        "timestamp": data.get("timestamp"), "source": "text",
    })

    if not audio_loop:
        print("[SERVER DEBUG] [Error] Audio loop is None. Cannot send text.")
        return {"accepted": False, "reason": "audio_loop_unavailable", "message": message}

    if not getattr(audio_loop, "session", None) or not audio_loop.live_session_available():
        print("[SERVER DEBUG] [Error] Session is None. Cannot send text.")
        return {"accepted": False, "reason": "live_session_unavailable", "message": message}

    session = audio_loop.session

    if text:
        print(f"[SERVER DEBUG] Sending message to model: '{text}'")

        memory_result = conversation_context.build_context(text, channel="text")
        learning_result = conversational_memory.process_conversation_turn(
            text, channel="text", conversation_context=memory_result
        )
        relevant_memory = memory_result["context"]
        model_input = text
        if learning_result.get("confidence", 0) >= 0.98 and learning_result.get("action") == "created":
            model_input = (
                "System Notification: The user explicitly asked you to remember the following item. "
                "It has been persisted successfully. Confirm this briefly.\n\n"
                f"User message: {text}"
            )
        elif relevant_memory:
            model_input = (
                "System Notification: Relevant memory retrieval was already completed for this turn. "
                "Do not call retrieve_memory again for this request.\n"
                f"{relevant_memory}\n\nUser message: {text}"
            )
        
        # Log User Input to Project History
        if audio_loop and audio_loop.project_manager:
            audio_loop.project_manager.log_chat("User", text)
            
        # Use the same 'send' method that worked for audio, as 'send_realtime_input' and 'send_client_content' seem unstable in this env
        # INJECT VIDEO FRAME IF AVAILABLE (VAD-style logic for Text Input)
        if audio_loop and audio_loop._latest_image_payload:
            print(f"[SERVER DEBUG] Piggybacking video frame with text input.")
            try:
                # Send frame first
                await session.send(input=audio_loop._latest_image_payload, end_of_turn=False)
            except Exception as e:
                print(f"[SERVER DEBUG] Failed to send piggyback frame: {e}")
                
        try:
            await session.send(input=model_input, end_of_turn=True)
        except Exception as error:
            print(f"[VOICE_SESSION] user_input send failed: {type(error).__name__}: {error}")
            if audio_loop.invalidate_live_session(session=session, error=error):
                code, reason = audio_loop._connection_error_details(error)
                audio_loop._publish_connection_state("closed", code=code, reason=reason)
            return {"accepted": False, "reason": "live_send_failed", "message": message}
        print(f"[SERVER DEBUG] Message sent to model successfully.")
        return {"accepted": True, "message": message}


@sio.event
async def get_chat_history(sid, data=None):
    messages = await asyncio.to_thread(chat_history.list_messages)
    payload = {"messages": messages}
    await sio.emit("chat_history", payload, room=sid)
    return payload

import json
from datetime import datetime
from pathlib import Path

# ... (imports)

@sio.event
async def video_frame(sid, data):
    # data should contain 'image' which is binary (blob) or base64 encoded
    image_data = data.get('image')
    if image_data and audio_loop:
        # We don't await this because we don't want to block the socket handler
        # But send_frame is async, so we create a task
        asyncio.create_task(audio_loop.send_frame(image_data))

@sio.event
async def save_memory(sid, data):
    try:
        messages = data.get('messages', [])
        if not messages:
            print("No messages to save.")
            return

        # Ensure directory exists
        memory_dir = Path("long_term_memory")
        memory_dir.mkdir(exist_ok=True)

        # Generate filename
        # Use provided filename if available, else timestamp
        provided_name = data.get('filename')
        
        if provided_name:
            # Simple sanitization
            if not provided_name.endswith('.txt'):
                provided_name += '.txt'
            # Prevent directory traversal
            filename = memory_dir / Path(provided_name).name 
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = memory_dir / f"memory_{timestamp}.txt"

        # Write to file
        with open(filename, 'w', encoding='utf-8') as f:
            for msg in messages:
                sender = msg.get('sender', 'Unknown')
                text = msg.get('text', '')
        print(f"Conversation saved to {filename}")
        await sio.emit('status', {'msg': 'Memory Saved Successfully'})

    except Exception as e:
        print(f"Error saving memory: {e}")
        await sio.emit('error', {'msg': f"Failed to save memory: {str(e)}"})

@sio.event
async def upload_memory(sid, data):
    print("Received persistent memory import request")
    try:
        memory_text = data.get('memory', '')
        if not memory_text:
            await sio.emit('memory_import_result', {
                'success': False,
                'error': 'Memory file is empty.'
            }, room=sid)
            return

        result = personal_memory.import_memory_text(
            memory_text,
            source_name=data.get('source_name')
        )
        await sio.emit('memory_import_result', result, room=sid)

    except (TypeError, ValueError) as e:
        print(f"Memory import rejected: {e}")
        await sio.emit('memory_import_result', {
            'success': False,
            'error': str(e)
        }, room=sid)
    except Exception as e:
        print(f"Error uploading memory: {e}")
        await sio.emit('memory_import_result', {
            'success': False,
            'error': 'Memory import failed. Existing memory was preserved.'
        }, room=sid)

@sio.event
async def discover_kasa(sid):
    print(f"Received discover_kasa request")
    try:
        devices = await kasa_agent.discover_devices()
        await sio.emit('kasa_devices', devices)
        await sio.emit('status', {'msg': f"Found {len(devices)} Kasa devices"})
        
        # Save to settings
        # devices is a list of full device info dicts. minimizing for storage.
        saved_devices = []
        for d in devices:
            saved_devices.append({
                "ip": d["ip"],
                "alias": d["alias"],
                "model": d["model"]
            })
        
        # Merge with existing to preserve any manual overrides? 
        # For now, just overwrite with latest scan result + previously known if we want to be fancy,
        # but user asked for "Any new devices that are scanned are added there".
        # A simple full persistence of current state is safest.
        SETTINGS["kasa_devices"] = saved_devices
        save_settings()
        print(f"[SERVER] Saved {len(saved_devices)} Kasa devices to settings.")
        
    except Exception as e:
        print(f"Error discovering kasa: {e}")
        await sio.emit('error', {'msg': f"Kasa Discovery Failed: {str(e)}"})

@sio.event
async def iterate_cad(sid, data):
    # data: { prompt: "make it bigger" }
    prompt = data.get('prompt')
    print(f"Received iterate_cad request: '{prompt}'")
    
    if not audio_loop or not audio_loop.cad_agent:
        await sio.emit('error', {'msg': "CAD Agent not available"})
        return

    try:
        # Notify user work has started
        await sio.emit('status', {'msg': 'Iterating design...'})
        await sio.emit('cad_status', {'status': 'generating'})
        
        # Call the agent with project path
        cad_output_dir = str(audio_loop.project_manager.get_current_project_path() / "cad")
        result = await audio_loop.cad_agent.iterate_prototype(prompt, output_dir=cad_output_dir)
        
        if result:
            info = f"{len(result.get('data', ''))} bytes (STL)"
            print(f"Sending updated CAD data: {info}")
            await sio.emit('cad_data', result)
            # Save to Project
            if 'file_path' in result:
                saved_path = audio_loop.project_manager.save_cad_artifact(result['file_path'], prompt)
                if saved_path:
                    print(f"[SERVER] Saved iterated CAD to {saved_path}")

            await sio.emit('status', {'msg': 'Design updated'})
        else:
            await sio.emit('error', {'msg': 'Failed to update design'})
            
    except Exception as e:
        print(f"Error iterating CAD: {e}")
        await sio.emit('error', {'msg': f"Iteration Error: {str(e)}"})

@sio.event
async def generate_cad(sid, data):
    # data: { prompt: "make a cube" }
    prompt = data.get('prompt')
    print(f"Received generate_cad request: '{prompt}'")
    
    if not audio_loop or not audio_loop.cad_agent:
        await sio.emit('error', {'msg': "CAD Agent not available"})
        return

    try:
        await sio.emit('status', {'msg': 'Generating new design...'})
        await sio.emit('cad_status', {'status': 'generating'})
        
        # Use generate_prototype based on prompt with project path
        cad_output_dir = str(audio_loop.project_manager.get_current_project_path() / "cad")
        result = await audio_loop.cad_agent.generate_prototype(prompt, output_dir=cad_output_dir)
        
        if result:
            info = f"{len(result.get('data', ''))} bytes (STL)"
            print(f"Sending newly generated CAD data: {info}")
            await sio.emit('cad_data', result)


            # Save to Project
            if 'file_path' in result:
                saved_path = audio_loop.project_manager.save_cad_artifact(result['file_path'], prompt)
                if saved_path:
                    print(f"[SERVER] Saved generated CAD to {saved_path}")

            await sio.emit('status', {'msg': 'Design generated'})
        else:
            await sio.emit('error', {'msg': 'Failed to generate design'})
            
    except Exception as e:
        print(f"Error generating CAD: {e}")
        await sio.emit('error', {'msg': f"Generation Error: {str(e)}"})

@sio.event
async def prompt_web_agent(sid, data):
    # data: { prompt: "find xyz" }
    prompt = data.get('prompt')
    print(f"Received web agent prompt: '{prompt}'")
    
    if not audio_loop or not audio_loop.web_agent:
        await sio.emit('error', {'msg': "Web Agent not available"})
        return

    try:
        await sio.emit('status', {'msg': 'Web Agent running...'}, room=sid)
        
        async def update_frontend(image_b64, log_text):
            await sio.emit('browser_frame', {'image': image_b64, 'log': log_text}, room=sid)

        result = await audio_loop.web_agent.run_task(prompt, update_callback=update_frontend)
        if result.get("ok"):
            await sio.emit('status', {'msg': 'Web Agent finished'}, room=sid)
        else:
            code = result.get("error", {}).get("code", "web_agent_unavailable")
            await sio.emit('error', {'msg': f"Web Agent Error: {code}"}, room=sid)
        return result
        
    except Exception as e:
        print(f"Error running Web Agent: {e}")
        await sio.emit('error', {'msg': f"Web Agent Error: {str(e)}"})

@sio.event
async def discover_printers(sid):
    print("Received discover_printers request")
    
    # If audio_loop isn't ready yet, return saved printers from settings
    if not audio_loop or not audio_loop.printer_agent:
        saved_printers = SETTINGS.get("printers", [])
        if saved_printers:
            # Convert saved printers to the expected format
            printer_list = []
            for p in saved_printers:
                printer_list.append({
                    "name": p.get("name", p["host"]),
                    "host": p["host"],
                    "port": p.get("port", 80),
                    "printer_type": p.get("type", "unknown"),
                    "camera_url": p.get("camera_url")
                })
            print(f"[SERVER] Returning {len(printer_list)} saved printers (audio_loop not ready)")
            await sio.emit('printer_list', printer_list)
            return
        else:
            await sio.emit('printer_list', [])
            await sio.emit('status', {'msg': "Connect to A.D.A to enable printer discovery"})
            return
        
    try:
        printers = await audio_loop.printer_agent.discover_printers()
        await sio.emit('printer_list', printers)
        await sio.emit('status', {'msg': f"Found {len(printers)} printers"})
    except Exception as e:
        print(f"Error discovering printers: {e}")
        await sio.emit('error', {'msg': f"Printer Discovery Failed: {str(e)}"})

@sio.event
async def add_printer(sid, data):
    # data: { host: "192.168.1.50", name: "My Printer", type: "moonraker" }
    raw_host = data.get('host')
    name = data.get('name') or raw_host
    ptype = data.get('type', "moonraker")
    
    # Parse port if present
    if ":" in raw_host:
        host, port_str = raw_host.split(":")
        port = int(port_str)
    else:
        host = raw_host
        port = 80
    
    print(f"Received add_printer request: {host}:{port} ({ptype})")
    
    if not audio_loop or not audio_loop.printer_agent:
        await sio.emit('error', {'msg': "Printer Agent not available"})
        return
        
    try:
        # Add manually
        camera_url = data.get('camera_url')
        printer = audio_loop.printer_agent.add_printer_manually(name, host, port=port, printer_type=ptype, camera_url=camera_url)
        
        # Save to settings
        new_printer_config = {
            "name": name,
            "host": host,
            "port": port,
            "type": ptype,
            "camera_url": camera_url
        }
        
        # Check if already exists to avoid duplicates
        exists = False
        for p in SETTINGS.get("printers", []):
            if p["host"] == host and p["port"] == port:
                exists = True
                break
        
        if not exists:
            if "printers" not in SETTINGS:
                SETTINGS["printers"] = []
            SETTINGS["printers"].append(new_printer_config)
            save_settings()
            print(f"[SERVER] Saved printer {name} to settings.")
        
        # Probe to confirm/correct type
        print(f"Probing {host} to confirm type...")
        # Try port 7125 (Moonraker) and 4408 (Fluidd/K1) 
        ports_to_try = [80, 7125, 4408]
        
        actual_type = "unknown"
        for port in ports_to_try:
             found_type = await audio_loop.printer_agent._probe_printer_type(host, port)
             if found_type.value != "unknown":
                 actual_type = found_type
                 # Update port if different
                 if port != 80:
                     printer.port = port
                 break
        
        if actual_type != "unknown" and actual_type != printer.printer_type:
             printer.printer_type = actual_type
             print(f"Corrected type to {actual_type.value} on port {printer.port}")
             
        # Refresh list for everyone
        printers = [p.to_dict() for p in audio_loop.printer_agent.printers.values()]
        await sio.emit('printer_list', printers)
        await sio.emit('status', {'msg': f"Added printer: {name}"})
        
    except Exception as e:
        print(f"Error adding printer: {e}")
        await sio.emit('error', {'msg': f"Failed to add printer: {str(e)}"})

@sio.event
async def print_stl(sid, data):
    print(f"Received print_stl request: {data}")
    # data: { stl_path: "path/to.stl" | "current", printer: "name_or_ip", profile: "optional" }
    
    if not audio_loop or not audio_loop.printer_agent:
        await sio.emit('error', {'msg': "Printer Agent not available"})
        return
        
    try:
        stl_path = data.get('stl_path', 'current')
        printer_name = data.get('printer')
        profile = data.get('profile')
        
        if not printer_name:
             await sio.emit('error', {'msg': "No printer specified"})
             return
             
        await sio.emit('status', {'msg': f"Preparing print for {printer_name}..."})
        
        # Get current project path for resolution
        current_project_path = None
        if audio_loop and audio_loop.project_manager:
            current_project_path = str(audio_loop.project_manager.get_current_project_path())
            print(f"[SERVER DEBUG] Using project path: {current_project_path}")

        # Resolve STL path before slicing so we can preview it
        resolved_stl = audio_loop.printer_agent._resolve_file_path(stl_path, current_project_path)
        
        if resolved_stl and os.path.exists(resolved_stl):
            # Open the STL in the CAD module for preview
            try:
                import base64
                with open(resolved_stl, 'rb') as f:
                    stl_data = f.read()
                stl_b64 = base64.b64encode(stl_data).decode('utf-8')
                stl_filename = os.path.basename(resolved_stl)
                
                print(f"[SERVER] Opening STL in CAD module: {stl_filename}")
                await sio.emit('cad_data', {
                    'format': 'stl',
                    'data': stl_b64,
                    'filename': stl_filename
                })
            except Exception as e:
                print(f"[SERVER] Warning: Could not preview STL: {e}")
        
        # Progress Callback
        async def on_slicing_progress(percent, message):
            await sio.emit('slicing_progress', {
                'printer': printer_name,
                'percent': percent,
                'message': message
            })
            if percent < 100:
                 await sio.emit('status', {'msg': f"Slicing: {percent}%"})

        result = await audio_loop.printer_agent.print_stl(
            stl_path, 
            printer_name, 
            profile,
            progress_callback=on_slicing_progress,
            root_path=current_project_path
        )
        
        await sio.emit('print_result', result)
        await sio.emit('status', {'msg': f"Print Job: {result.get('status', 'unknown')}"})
        
    except Exception as e:
        print(f"Error printing STL: {e}")
        await sio.emit('error', {'msg': f"Print Failed: {str(e)}"})

@sio.event
async def get_slicer_profiles(sid):
    """Get available OrcaSlicer profiles for manual selection."""
    print("Received get_slicer_profiles request")
    if not audio_loop or not audio_loop.printer_agent:
        await sio.emit('error', {'msg': "Printer Agent not available"})
        return
    
    try:
        profiles = audio_loop.printer_agent.get_available_profiles()
        await sio.emit('slicer_profiles', profiles)
    except Exception as e:
        print(f"Error getting slicer profiles: {e}")
        await sio.emit('error', {'msg': f"Failed to get profiles: {str(e)}"})

@sio.event
async def control_kasa(sid, data):
    # data: { ip, action: "on"|"off"|"brightness"|"color", value: ... }
    ip = data.get('ip')
    action = data.get('action')
    print(f"Kasa Control: {ip} -> {action}")
    
    try:
        success = False
        if action == "on":
            success = await kasa_agent.turn_on(ip)
        elif action == "off":
            success = await kasa_agent.turn_off(ip)
        elif action == "brightness":
            val = data.get('value')
            success = await kasa_agent.set_brightness(ip, val)
        elif action == "color":
            # value is {h, s, v} - convert to tuple for set_color
            h = data.get('value', {}).get('h', 0)
            s = data.get('value', {}).get('s', 100)
            v = data.get('value', {}).get('v', 100)
            success = await kasa_agent.set_color(ip, (h, s, v))
        
        if success:
            await sio.emit('kasa_update', {
                'ip': ip,
                'is_on': True if action == "on" else (False if action == "off" else None),
                'brightness': data.get('value') if action == "brightness" else None,
            })
 
        else:
             await sio.emit('error', {'msg': f"Failed to control device {ip}"})

    except Exception as e:
         print(f"Error controlling kasa: {e}")
         await sio.emit('error', {'msg': f"Kasa Control Error: {str(e)}"})

@sio.event
async def get_settings(sid):
    await sio.emit('settings', SETTINGS)

@sio.event
async def update_settings(sid, data):
    # Generic update
    print(f"Updating settings: {data}")
    
    # Handle specific keys if needed
    if "tool_permissions" in data:
        SETTINGS["tool_permissions"].update(data["tool_permissions"])
        if audio_loop:
            audio_loop.update_permissions(SETTINGS["tool_permissions"])
            
    if "face_auth_enabled" in data:
        SETTINGS["face_auth_enabled"] = data["face_auth_enabled"]
        # If turned OFF, maybe emit auth status true?
        if not data["face_auth_enabled"]:
             await sio.emit('auth_status', {'authenticated': True})
             # Stop auth loop if running?
             if authenticator:
                 authenticator.stop() 

    if "camera_flipped" in data:
        SETTINGS["camera_flipped"] = data["camera_flipped"]
        print(f"[SERVER] Camera flip set to: {data['camera_flipped']}")

    if "speaker_labels" in data and isinstance(data["speaker_labels"], list):
        SETTINGS["speaker_labels"] = list(dict.fromkeys(
            str(label).strip() for label in data["speaker_labels"] if str(label).strip()
        ))

    if "output_device_name" in data:
        requested_name = data["output_device_name"]
        if requested_name in SETTINGS.get("speaker_labels", []):
            SETTINGS["output_device_name"] = requested_name
            print(f"[SPEAKER_BASELINE] source=ui output_device_name={requested_name!r} applies=next_session")
        else:
            print(f"[SPEAKER_BASELINE] source=ui rejected unknown output_device_name={requested_name!r}")

    save_settings()
    # Broadcast new full settings
    await sio.emit('settings', SETTINGS)


# Deprecated/Mapped for compatibility if frontend still uses specific events
@sio.event
async def get_tool_permissions(sid):
    await sio.emit('tool_permissions', SETTINGS["tool_permissions"])

@sio.event
async def update_tool_permissions(sid, data):
    print(f"Updating permissions (legacy event): {data}")
    SETTINGS["tool_permissions"].update(data)
    save_settings()
    
    if audio_loop:
        audio_loop.update_permissions(SETTINGS["tool_permissions"])
    # Broadcast update to all
    await sio.emit('tool_permissions', SETTINGS["tool_permissions"])


@sio.event
async def get_integrations(sid):
    await sio.emit(
        "integration_registry",
        {"integrations": integration_manager.list_integrations()},
        room=sid,
    )


@sio.event
async def get_integration_details(sid, data=None):
    request = data or {}
    try:
        details = integration_manager.get_details(
            request.get("integration_id", "gemini"),
            period=request.get("period", "today"),
            start_date=request.get("start_date"),
            end_date=request.get("end_date"),
        )
        await sio.emit("integration_details", details, room=sid)
    except (KeyError, ValueError) as error:
        await sio.emit(
            "integration_action_error",
            {"message": str(error)[:300]},
            room=sid,
        )


@sio.event
async def refresh_integration_status(sid, data=None):
    request = data or {}
    try:
        details = integration_manager.get_details(
            request.get("integration_id", "gemini"),
            period=request.get("period", "today"),
            start_date=request.get("start_date"),
            end_date=request.get("end_date"),
        )
        await sio.emit("integration_details", details, room=sid)
    except (KeyError, ValueError) as error:
        await sio.emit(
            "integration_action_error",
            {"message": str(error)[:300]},
            room=sid,
        )


@sio.event
async def get_integration_reports(sid, data=None):
    request = data or {}
    try:
        payload = integration_manager.get_reports(
            request.get("integration_id", "gemini"),
            limit=request.get("limit", 20),
        )
        await sio.emit("integration_reports", payload, room=sid)
    except (KeyError, ValueError) as error:
        await sio.emit(
            "integration_action_error",
            {"message": str(error)[:300]},
            room=sid,
        )


@sio.event
async def list_system_incidents(sid, data=None):
    request = data or {}
    payload = integration_manager.list_system_incidents(
        severity=request.get("severity"), status=request.get("status", "abertos"),
        period=request.get("period"),
    )
    await sio.emit("system_incidents", payload, room=sid)


@sio.event
async def get_incident_details(sid, data=None):
    payload = integration_manager.get_incident_details((data or {}).get("incident_id", ""))
    await sio.emit("incident_details", payload, room=sid)


def build_incident_model_input(incident):
    """Build a bounded operational context without personal-memory content."""
    fields = (
        "incident_id", "severity", "source", "component", "error_code", "safe_summary",
        "status", "occurrence_count", "first_seen", "last_seen", "diagnosis",
    )
    context = {key: incident.get(key) for key in fields if incident.get(key) is not None}
    return (
        "System Notification: explain this authoritative sanitized operational incident. "
        "Separate OBSERVED FACTS from CAUSE HYPOTHESES; never invent absent evidence.\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


@sio.event
async def ask_veronica_about_incident(sid, data=None):
    if not audio_loop or not audio_loop.session:
        return {"accepted": False, "reason": "live_session_unavailable"}
    raw_id = (data or {}).get("incident_id")
    try:
        incident_id = str(uuid.UUID(str(raw_id)))
    except (ValueError, TypeError, AttributeError):
        return {"accepted": False, "reason": "invalid_incident_id"}
    result = integration_manager.tool_get_incident_details(incident_id)
    incident = result.get("incident")
    if not incident:
        return {"accepted": False, "reason": "incident_not_found"}
    try:
        await audio_loop.session.send(input=build_incident_model_input(incident), end_of_turn=True)
    except Exception:
        return {"accepted": False, "reason": "live_send_failed"}
    return {"accepted": True, "incident_id": incident_id}


@sio.event
async def test_integration_connection(sid, data=None):
    request = data or {}
    result = await integration_manager.test_connection(
        request.get("integration_id", "gemini")
    )
    await sio.emit("integration_test_result", result, room=sid)


@sio.event
async def update_gemini_api_key(sid, data=None):
    # The key is accepted only in this backend call and is never logged or echoed.
    try:
        await integration_manager.update_api_key((data or {}).get("api_key", ""))
        await sio.emit(
            "integration_key_result",
            {"success": True, "configured": True, "restart_required_for_live": True},
            room=sid,
        )
    except (OSError, ValueError) as error:
        await sio.emit(
            "integration_key_result",
            {"success": False, "configured": False, "error": str(error)[:300]},
            room=sid,
        )


@sio.event
async def update_integration_budget(sid, data=None):
    request = data or {}
    integration_id = request.get("integration_id", "gemini")
    try:
        integration_manager.update_monthly_token_budget(
            integration_id, request.get("monthly_token_budget")
        )
        await sio.emit(
            "integration_details",
            integration_manager.get_details(integration_id),
            room=sid,
        )
    except (KeyError, ValueError, OSError) as error:
        await sio.emit(
            "integration_action_error",
            {"message": str(error)[:300]},
            room=sid,
        )

if __name__ == "__main__":
    uvicorn.run(
        "server:app_socketio", 
        host="127.0.0.1", 
        port=8000, 
        reload=False, # Reload enabled causes spawn of worker which might miss the event loop policy patch
        loop="asyncio",
        reload_excludes=["temp_cad_gen.py", "output.stl", "*.stl"]
    )
