// web/static/chat.js
// Agent Chat Component - Clean Vue implementation

const ChatComponent = {
    template: `
        <div class="chat-container">
            <div class="panel-header">
                <h2>Agent Chat</h2>
                <button @click="clearChat" class="btn-small">Clear</button>
            </div>
            
            <div class="chat-messages" ref="messagesContainer">
                <div v-if="messages.length === 0" class="empty-state">
                    <p>Ask me about security events, investigate alerts, or request actions.</p>
                </div>
                
                <!-- Messages -->
                <div v-for="msg in messages" :key="msg.id" class="chat-msg" :class="msg.role">
                    <div v-if="msg.role === 'user'" class="msg-bubble user">{{ msg.content }}</div>
                    <div v-else-if="msg.role === 'assistant'" class="msg-bubble assistant" v-html="renderMarkdown(msg.content)"></div>
                    <div v-else-if="msg.role === 'terminal'" class="terminal-output">
                        <div class="terminal-header">Terminal Output</div>
                        <pre class="terminal-content">{{ msg.content }}</pre>
                    </div>
                </div>
                
                <!-- Streaming terminal with integrated input -->
                <div v-if="showingTerminal || terminalLines.length > 0 || terminalInputNeeded" class="terminal-output streaming">
                    <div class="terminal-header">{{ terminalInputNeeded ? (terminalInputType === 'password' ? 'Authentication Required' : 'Input Required') : 'Running...' }}</div>
                    <pre class="terminal-content">{{ terminalLines.length > 0 ? terminalLines.join('\\n') : 'Waiting for output...' }}</pre>
                    <div v-if="terminalInputNeeded" class="terminal-input-row">
                        <span class="terminal-prompt">▶</span>
                        <template v-if="terminalInputType === 'password'">
                            <input 
                                type="password" 
                                v-model="terminalPassword"
                                @keyup.enter="retryWithPassword"
                                class="terminal-input-field"
                                placeholder="Enter password..."
                                ref="terminalInput"
                            >
                            <button @click="retryWithPassword" class="btn-terminal-submit">↵</button>
                        </template>
                        <template v-else-if="terminalInputType === 'confirm'">
                            <button @click="sendTerminalInput('y')" class="btn-confirm-yes">Yes</button>
                            <button @click="sendTerminalInput('n')" class="btn-confirm-no">No</button>
                        </template>
                        <template v-else>
                            <input 
                                type="text" 
                                v-model="terminalPassword"
                                @keyup.enter="retryWithPassword"
                                class="terminal-input-field"
                                placeholder="Enter input..."
                                ref="terminalInput"
                            >
                            <button @click="retryWithPassword" class="btn-terminal-submit">↵</button>
                        </template>
                    </div>
                </div>
                
                <!-- Proposal card -->
                <div v-if="proposal" class="action-card">
                    <div class="action-header">Proposed Action</div>
                    <div class="action-content">
                        <code class="command-code">{{ proposal.command }}</code>
                        <p class="action-reason">{{ proposal.reason }}</p>
                    </div>
                    <div class="action-buttons">
                        <button @click="executeProposal" class="btn-run" :disabled="executing">
                            {{ executing ? 'Running...' : 'Run' }}
                        </button>
                        <button @click="cancelProposal" class="btn-cancel">Cancel</button>
                    </div>
                </div>
                
                <!-- Investigation card for flagged events -->
                <div v-if="investigationFlag" class="investigation-card">
                    <div class="investigation-header">
                        <span class="investigation-severity" :class="investigationFlag.severity">{{ investigationFlag.severity || 'ALERT' }}</span>
                        <span class="investigation-time">{{ investigationFlag.timestamp }}</span>
                    </div>
                    <div class="investigation-content">
                        <div class="investigation-summary">{{ investigationFlag.summary }}</div>
                        <div v-if="investigationFlag.event_ids && investigationFlag.event_ids.length" class="investigation-meta">
                            {{ investigationFlag.event_ids.length }} related event(s)
                        </div>
                        <div v-if="investigationFlag.suggested_actions && investigationFlag.suggested_actions.length" class="investigation-actions">
                            <strong>Suggested:</strong> {{ investigationFlag.suggested_actions.join(', ') }}
                        </div>
                    </div>
                    <div class="investigation-buttons">
                        <button @click="returnToPending" class="btn-return-pending">Put Back</button>
                        <button @click="dismissInvestigation" class="btn-cancel">Dismiss</button>
                        <button @click="startInvestigation" class="btn-investigate-start">Start Investigation</button>
                    </div>
                </div>
                
                <!-- Status indicator - pulsing, left aligned -->
                <div v-if="status || executing" class="status-indicator">
                    <span class="status-pulse"></span>
                    <span class="status-text">{{ status || 'Running command...' }}</span>
                </div>
            </div>
            
            <div class="chat-input-area">
                <input 
                    type="text" 
                    v-model="input" 
                    @keyup.enter="sendMessage" 
                    placeholder="Ask Agent..."
                    :disabled="loading"
                >
                <button @click="sendMessage" class="btn-send" :disabled="loading || !input.trim()">
                    Send
                </button>
            </div>
        </div>
    `,

    props: ['apiKey'],

    data() {
        return {
            messages: [],
            input: '',
            loading: false,
            status: '',
            proposal: null,
            executing: false,
            showingTerminal: false,  // Controls terminal visibility during execution
            terminalLines: [],
            terminalInputNeeded: false,
            terminalInputType: null,  // 'password' or 'confirm'
            terminalPassword: '',
            pendingCommand: '',
            investigationFlag: null,
            terminalSession: null,  // WebSocket connection
            terminalPromptHint: null,  // Hint from PTY service
            lastMessageTime: 0  // For idle timeout
        };
    },

    mounted() {
        // Listen for investigate-flag events from main app
        window.addEventListener('investigate-flag', (e) => {
            if (e.detail) {
                this.setInvestigationFlag(e.detail);
            }
        });
    },

    methods: {
        renderMarkdown(text) {
            if (!text) return '';
            try {
                // Use marked with DOMPurify
                const html = marked.parse(String(text));
                return typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
            } catch (e) {
                console.error('Markdown error:', e);
                return this.escapeHtml(text);
            }
        },

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        },

        scrollToBottom() {
            this.$nextTick(() => {
                const container = this.$refs.messagesContainer;
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            });
        },

        async sendMessage() {
            if (!this.input.trim() || this.loading) return;

            const message = this.input.trim();
            this.input = '';
            this.loading = true;
            this.status = '';

            // Add user message
            this.messages.push({
                id: Date.now(),
                role: 'user',
                content: message
            });
            this.scrollToBottom();

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': this.apiKey
                    },
                    body: JSON.stringify({ message })
                });

                await this.processSSE(response);
            } catch (e) {
                this.messages.push({
                    id: Date.now(),
                    role: 'assistant',
                    content: `Error: ${e.message}`
                });
            }

            this.loading = false;
            this.status = '';
            this.scrollToBottom();
        },

        async processSSE(response) {
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                let eventType = 'message';
                for (const line of lines) {
                    if (line.startsWith('event:')) {
                        eventType = line.slice(6).trim();
                    } else if (line.startsWith('data:')) {
                        try {
                            const data = JSON.parse(line.slice(5).trim());
                            this.handleSSEEvent(eventType, data);
                        } catch (e) {
                            // Ignore parse errors
                        }
                    }
                }
            }
        },

        handleSSEEvent(eventType, data) {
            console.log('SSE:', eventType, data);

            switch (data.event || eventType) {
                case 'status':
                    this.status = data.text || '';
                    break;

                case 'text':
                    this.messages.push({
                        id: Date.now(),
                        role: 'assistant',
                        content: data.content
                    });
                    this.scrollToBottom();
                    break;

                case 'proposal':
                    this.proposal = {
                        command: data.command,
                        reason: data.reason
                    };
                    this.status = '';
                    this.scrollToBottom();
                    break;

                case 'terminal':
                    this.terminalLines.push(data.line);
                    this.scrollToBottom();
                    break;

                case 'terminal_input_needed':
                    // Show the prompt text in terminal
                    if (data.prompt) {
                        this.terminalLines.push(data.prompt);
                    }
                    this.terminalInputNeeded = true;
                    this.pendingCommand = data.command;
                    this.executing = false;  // Allow input
                    this.status = '';  // Clear status
                    this.scrollToBottom();
                    // Focus the input field
                    this.$nextTick(() => {
                        if (this.$refs.terminalInput) {
                            this.$refs.terminalInput.focus();
                        }
                    });
                    break;

                case 'terminal_done':
                    // Only save as message if no input is needed
                    if (!data.needs_input && this.terminalLines.length > 0) {
                        this.messages.push({
                            id: Date.now(),
                            role: 'terminal',
                            content: this.terminalLines.join('\n')
                        });
                        this.terminalLines = [];
                        this.terminalInputNeeded = false;
                    }
                    break;

                case 'done':
                    this.status = '';
                    break;
            }
        },

        async executeProposal() {
            if (!this.proposal || this.executing) return;

            const command = this.proposal.command;
            this.proposal = null;
            this.executing = true;
            this.showingTerminal = true;  // Show terminal immediately
            this.terminalLines = [];
            this.terminalInputNeeded = false;
            this.terminalInputType = null;
            this.pendingCommand = command;
            this.status = 'Preparing terminal...';

            try {
                // Step 1: Prepare the command and get a command_id
                const prepResponse = await fetch('/api/terminal/prepare', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': this.apiKey
                    },
                    body: JSON.stringify({ command })
                });

                if (!prepResponse.ok) {
                    throw new Error('Failed to prepare command');
                }

                const { command_id } = await prepResponse.json();

                // Step 2: Connect via WebSocket
                const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/terminal/${command_id}`);
                this.terminalSession = ws;

                this.status = 'Connecting...';

                ws.onopen = () => {
                    this.status = 'Running...';
                    this.lastMessageTime = Date.now();
                };

                // Safety timeout only - wait for 'done' event, but close after 5 minutes as fallback
                let safetyTimeout = setTimeout(() => {
                    console.log('[PTY] Safety timeout (5 min) - closing connection');
                    ws.close();
                }, 5 * 60 * 1000);

                ws.onmessage = (event) => {
                    const msg = JSON.parse(event.data);
                    console.log('[PTY] Message received:', msg);
                    this.lastMessageTime = Date.now();
                    this.handleTerminalMessage(msg);
                };

                ws.onerror = (error) => {
                    console.error('[PTY] WebSocket error:', error);
                    clearTimeout(safetyTimeout);
                    this.terminalLines.push('Connection error');
                    this.executing = false;
                    this.status = '';
                };

                ws.onclose = () => {
                    console.log('[PTY] WebSocket closed');
                    clearTimeout(safetyTimeout);
                    // Save terminal output as message if we have content
                    if (this.terminalLines.length > 0 && !this.terminalInputNeeded) {
                        this.messages.push({
                            id: Date.now(),
                            role: 'terminal',
                            content: this.terminalLines.join('\n')
                        });
                        this.terminalLines = [];
                    }
                    this.terminalSession = null;
                    this.executing = false;
                    this.status = '';
                    this.scrollToBottom();
                };

            } catch (e) {
                this.messages.push({
                    id: Date.now(),
                    role: 'assistant',
                    content: `Execution error: ${e.message}`
                });
                this.executing = false;
                this.status = '';
            }

            this.scrollToBottom();
        },

        handleTerminalMessage(msg) {
            console.log('[PTY] Handling message:', msg.event);
            const event = msg.event;

            switch (event) {
                case 'session_created':
                    this.status = 'Running...';
                    break;

                case 'output':
                    // Add output to terminal display
                    if (msg.data) {
                        // Split by newlines and add each line
                        const lines = msg.data.split('\n');
                        for (const line of lines) {
                            if (line || this.terminalLines.length > 0) {
                                this.terminalLines.push(line);
                            }
                        }
                    }

                    // Check for password/confirm prompts
                    if (msg.prompt_hint === 'password') {
                        this.terminalInputNeeded = true;
                        this.terminalInputType = 'password';
                        this.executing = false;
                        this.$nextTick(() => {
                            if (this.$refs.terminalInput) {
                                this.$refs.terminalInput.focus();
                            }
                        });
                    } else if (msg.prompt_hint === 'confirm') {
                        this.terminalInputNeeded = true;
                        this.terminalInputType = 'confirm';
                        this.executing = false;
                        this.$nextTick(() => {
                            if (this.$refs.terminalInput) {
                                this.$refs.terminalInput.focus();
                            }
                        });
                    }

                    this.scrollToBottom();
                    break;

                case 'done':
                    console.log('[PTY] Done event received, exit_code:', msg.exit_code);
                    // Save terminal output 
                    const terminalOutput = this.terminalLines.join('\n');
                    if (terminalOutput) {
                        this.messages.push({
                            id: Date.now(),
                            role: 'terminal',
                            content: terminalOutput
                        });
                        this.terminalLines = [];
                    }
                    this.status = '';
                    this.executing = false;
                    this.showingTerminal = false;  // Hide terminal after saving output
                    this.scrollToBottom();
                    // Close the WebSocket
                    if (this.terminalSession) {
                        this.terminalSession.close();
                    }
                    // Now analyze the output with LLM (for both success and errors)
                    if (terminalOutput) {
                        this.analyzeCommandOutput(terminalOutput);
                    }
                    break;

                case 'error':
                    this.terminalLines.push(`Error: ${msg.message}`);
                    this.status = '';
                    this.executing = false;
                    break;
            }
        },

        async retryWithPassword() {
            if (!this.terminalPassword || !this.terminalSession) return;

            const input = this.terminalPassword + '\n';

            // Send input to PTY via WebSocket
            this.terminalSession.send(JSON.stringify({
                type: 'input',
                data: input
            }));

            // Clear password field but keep terminal
            this.terminalPassword = '';
            this.terminalInputNeeded = false;
            this.terminalInputType = null;
            this.executing = true;
            this.status = 'Authenticating...';
        },

        sendTerminalInput(value) {
            // Generic input sender for confirm prompts
            if (!this.terminalSession) return;

            this.terminalSession.send(JSON.stringify({
                type: 'input',
                data: value + '\n'
            }));

            this.terminalInputNeeded = false;
            this.terminalInputType = null;
            this.executing = true;
        },

        async analyzeCommandOutput(output) {
            // Send command output to LLM for analysis/summarization
            this.status = 'Analyzing output...';
            this.loading = true;

            try {
                // Truncate output if too long
                const truncatedOutput = output.length > 3000
                    ? output.substring(0, 3000) + '\n... (output truncated)'
                    : output;

                const analysisPrompt = `Command output:\n\`\`\`\n${truncatedOutput}\n\`\`\`\n\nProvide a brief, helpful analysis of this output.`;

                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': this.apiKey
                    },
                    body: JSON.stringify({ message: analysisPrompt })
                });

                if (!response.ok) {
                    throw new Error('Failed to analyze output');
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let assistantMessage = { id: Date.now(), role: 'assistant', content: '' };

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value);
                    const lines = chunk.split('\n');

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.slice(6));
                                if (data.event === 'text' && data.content) {
                                    if (!assistantMessage.content) {
                                        this.messages.push(assistantMessage);
                                    }
                                    assistantMessage.content += data.content;
                                    this.scrollToBottom();
                                }
                            } catch (e) { }
                        }
                    }
                }

                this.scrollToBottom();

            } catch (e) {
                console.error('Failed to analyze command output:', e);
            } finally {
                this.loading = false;
                this.status = '';
            }
        },

        cancelProposal() {
            this.proposal = null;
        },

        async clearChat() {
            try {
                await fetch('/api/chat/history', {
                    method: 'DELETE',
                    headers: { 'X-API-Key': this.apiKey }
                });
                this.messages = [];
                this.proposal = null;
                this.status = '';
                this.showingTerminal = false;
                this.terminalLines = [];
                this.terminalInputNeeded = false;
                this.terminalPassword = '';
                this.investigationFlag = null;
            } catch (e) {
                console.error('Clear error:', e);
            }
        },

        // Investigation card methods
        setInvestigationFlag(flag) {
            this.investigationFlag = flag;
            this.scrollToBottom();
        },

        startInvestigation() {
            if (!this.investigationFlag) return;

            const flag = this.investigationFlag;
            let message = `Investigate this flagged security event: ${flag.summary}`;
            message += `\n\nDetails:`;
            message += `\n- Severity: ${flag.severity || 'UNKNOWN'}`;
            message += `\n- Time: ${flag.timestamp || 'Unknown'}`;
            if (flag.event_ids && flag.event_ids.length) {
                message += `\n- Related Event IDs: ${flag.event_ids.join(', ')}`;
            }
            if (flag.suggested_actions && flag.suggested_actions.length) {
                message += `\n- Suggested Actions: ${flag.suggested_actions.join('; ')}`;
            }

            // Clear the investigation card and send message
            this.investigationFlag = null;
            this.input = message;
            this.sendMessage();
        },

        returnToPending() {
            if (!this.investigationFlag) return;
            // Dispatch event to return flag to pending panel
            window.dispatchEvent(new CustomEvent('return-to-pending', { detail: this.investigationFlag }));
            this.investigationFlag = null;
        },

        async dismissInvestigation() {
            if (!this.investigationFlag) return;
            // Dismiss the flag via API
            try {
                await fetch(`/api/flags/${this.investigationFlag.id}/dismiss`, {
                    method: 'POST',
                    headers: { 'X-API-Key': this.apiKey }
                });
            } catch (e) {
                console.error('Dismiss error:', e);
            }
            this.investigationFlag = null;
        }
    }
};

// Export for use
window.ChatComponent = ChatComponent;
