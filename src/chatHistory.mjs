export const chatSender = (role) => role === 'user' ? 'You' : role === 'assistant' ? 'VERÔNICA' : 'System';

export const chatMessage = (message) => {
    const timestamp = message.timestamp || new Date().toISOString();
    return {
        id: message.id,
        role: message.role,
        sender: message.sender || chatSender(message.role),
        text: message.content ?? message.text ?? '',
        timestamp,
        time: new Date(timestamp).toLocaleTimeString(),
        source: message.source || 'system',
        streaming: Boolean(message.streaming)
    };
};

export const mergeChatMessages = (current, incoming) => {
    const byId = new Map(current.map(message => [message.id, message]));
    incoming.forEach(message => {
        const normalized = chatMessage(message);
        byId.set(normalized.id, { ...byId.get(normalized.id), ...normalized });
    });
    return [...byId.values()].sort((left, right) =>
        String(left.timestamp).localeCompare(String(right.timestamp)) || String(left.id).localeCompare(String(right.id))
    );
};

export const newChatId = () => globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
