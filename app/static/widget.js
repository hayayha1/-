/**
 * 智语云 AI 智能客服挂件 JavaScript SDK (widget.js)
 * 支持免依赖嵌入任意第三方网页、电商商城、SaaS管理系统
 */
(function () {
  if (window.__CS_WIDGET_LOADED__) return;
  window.__CS_WIDGET_LOADED__ = true;

  // 1. 读取当前 script 标签属性
  const currentScript = document.currentScript || (function() {
    const scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  const defaultApiBase = window.location.origin;
  const config = {
    apiBase: currentScript ? (currentScript.getAttribute('data-api-base') || defaultApiBase) : defaultApiBase,
    tenantId: currentScript ? (currentScript.getAttribute('data-tenant') || 'tenant_default') : 'tenant_default',
    productCode: currentScript ? (currentScript.getAttribute('data-product') || 'DEFAULT_LINE') : 'DEFAULT_LINE',
    botName: currentScript ? (currentScript.getAttribute('data-bot-name') || '小智AI客服') : '小智AI客服',
    primaryColor: currentScript ? (currentScript.getAttribute('data-primary-color') || '#4F46E5') : '#4F46E5',
    welcomeMessage: currentScript ? (currentScript.getAttribute('data-welcome') || '您好！我是您的企业专属智能助手，请问有什么可以帮您？') : '您好！我是您的企业专属智能助手，请问有什么可以帮您？',
    botAvatar: currentScript ? (currentScript.getAttribute('data-avatar') || '🤖') : '🤖'
  };

  // 生成本地唯一访客 ID
  let customerId = localStorage.getItem('cs_widget_cust_id');
  if (!customerId) {
    customerId = 'cust_' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem('cs_widget_cust_id', customerId);
  }

  let sessionId = null;
  let isOpen = false;
  let messages = [
    {
      id: 'welcome',
      role: 'assistant',
      content: config.welcomeMessage,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
  ];

  // 2. 注入样式
  const styleEl = document.createElement('style');
  styleEl.innerHTML = `
    .cs-widget-container * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
    .cs-widget-btn {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 60px;
      height: 60px;
      border-radius: 30px;
      background: ${config.primaryColor};
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      cursor: pointer;
      box-shadow: 0 10px 25px rgba(0,0,0,0.2), 0 0 0 0 ${config.primaryColor}88;
      z-index: 999999;
      transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
      border: 2px solid rgba(255,255,255,0.4);
      user-select: none;
    }
    .cs-widget-btn:hover {
      transform: scale(1.08) translateY(-2px);
      box-shadow: 0 15px 30px rgba(0,0,0,0.25);
    }
    .cs-widget-badge {
      position: absolute;
      top: -4px;
      right: -4px;
      width: 14px;
      height: 14px;
      background: #EF4444;
      border: 2px solid #fff;
      border-radius: 50%;
    }
    .cs-widget-window {
      position: fixed;
      bottom: 96px;
      right: 24px;
      width: 380px;
      height: 560px;
      max-width: calc(100vw - 32px);
      max-height: calc(100vh - 120px);
      background: #ffffff;
      border-radius: 20px;
      box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25), 0 0 0 1px rgba(0,0,0,0.06);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      z-index: 999998;
      opacity: 0;
      transform: translateY(20px) scale(0.95);
      pointer-events: none;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .cs-widget-window.cs-open {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto;
    }
    .cs-widget-header {
      background: ${config.primaryColor};
      color: #fff;
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      user-select: none;
    }
    .cs-widget-header-left { display: flex; align-items: center; gap: 10px; }
    .cs-widget-header-avatar {
      width: 36px;
      height: 36px;
      border-radius: 12px;
      background: rgba(255,255,255,0.2);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
    }
    .cs-widget-header-title { font-size: 14px; font-weight: 700; line-height: 1.2; }
    .cs-widget-header-sub { font-size: 11px; opacity: 0.85; margin-top: 2px; }
    .cs-widget-header-close {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: rgba(255,255,255,0.15);
      border: none;
      color: #fff;
      font-size: 16px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
    }
    .cs-widget-header-close:hover { background: rgba(255,255,255,0.3); }
    .cs-widget-body {
      flex: 1;
      padding: 16px;
      overflow-y: auto;
      background: #F8FAFC;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .cs-widget-msg { display: flex; gap: 8px; max-width: 85%; }
    .cs-widget-msg.cs-user { align-self: flex-end; flex-direction: row-reverse; }
    .cs-widget-msg.cs-assistant { align-self: flex-start; }
    .cs-widget-msg-avatar {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: #E2E8F0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      flex-shrink: 0;
    }
    .cs-widget-msg-bubble {
      padding: 10px 14px;
      border-radius: 14px;
      font-size: 13px;
      line-height: 1.5;
      word-break: break-word;
    }
    .cs-widget-msg.cs-assistant .cs-widget-msg-bubble {
      background: #FFFFFF;
      color: #1E293B;
      border: 1px solid #E2E8F0;
      border-top-left-radius: 4px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .cs-widget-msg.cs-user .cs-widget-msg-bubble {
      background: ${config.primaryColor};
      color: #FFFFFF;
      border-top-right-radius: 4px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    }
    .cs-widget-msg-time { font-size: 10px; opacity: 0.6; margin-top: 4px; text-align: right; }
    .cs-widget-footer {
      padding: 12px 14px;
      background: #ffffff;
      border-top: 1px solid #E2E8F0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .cs-widget-input-row { display: flex; gap: 8px; align-items: center; }
    .cs-widget-input {
      flex: 1;
      height: 38px;
      border: 1px solid #CBD5E1;
      border-radius: 10px;
      padding: 0 12px;
      font-size: 13px;
      outline: none;
      transition: border-color 0.2s;
    }
    .cs-widget-input:focus { border-color: ${config.primaryColor}; }
    .cs-widget-send {
      height: 38px;
      padding: 0 16px;
      background: ${config.primaryColor};
      color: #fff;
      border: none;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    .cs-widget-send:disabled { opacity: 0.5; cursor: not-allowed; }
    .cs-widget-powered {
      font-size: 10px;
      text-align: center;
      color: #94A3B8;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
    }
    .cs-widget-typing {
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #94A3B8;
      margin: 0 1px;
      animation: csBounce 0.6s infinite alternate;
    }
    .cs-widget-typing:nth-child(2) { animation-delay: 0.2s; }
    .cs-widget-typing:nth-child(3) { animation-delay: 0.4s; }
    @keyframes csBounce {
      from { transform: translateY(0); }
      to { transform: translateY(-4px); }
    }
  `;
  document.head.appendChild(styleEl);

  // 3. 构建 DOM
  const root = document.createElement('div');
  root.className = 'cs-widget-container';
  root.innerHTML = `
    <!-- 悬浮启动图标 -->
    <div id="csWidgetBtn" class="cs-widget-btn" title="点击咨询智能客服">
      <span id="csWidgetBtnIcon">${config.botAvatar}</span>
      <span class="cs-widget-badge"></span>
    </div>

    <!-- 客服对话窗口 -->
    <div id="csWidgetWindow" class="cs-widget-window">
      <div class="cs-widget-header">
        <div class="cs-widget-header-left">
          <div class="cs-widget-header-avatar">${config.botAvatar}</div>
          <div>
            <div class="cs-widget-header-title">${config.botName}</div>
            <div class="cs-widget-header-sub">在线 · 毫秒级知识库响应</div>
          </div>
        </div>
        <button id="csWidgetClose" class="cs-widget-header-close" title="收起窗口">✕</button>
      </div>

      <div id="csWidgetBody" class="cs-widget-body"></div>

      <div class="cs-widget-footer">
        <div class="cs-widget-input-row">
          <input id="csWidgetInput" class="cs-widget-input" type="text" placeholder="输入您的问题..." autocomplete="off" />
          <button id="csWidgetSend" class="cs-widget-send">发送</button>
        </div>
        <div class="cs-widget-powered">
          <span>⚡</span>
          <span>由 智语云 SaaS AI 智能客服提供技术支持</span>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  const btn = document.getElementById('csWidgetBtn');
  const win = document.getElementById('csWidgetWindow');
  const closeBtn = document.getElementById('csWidgetClose');
  const body = document.getElementById('csWidgetBody');
  const input = document.getElementById('csWidgetInput');
  const sendBtn = document.getElementById('csWidgetSend');

  function renderMessages() {
    body.innerHTML = messages.map(m => `
      <div class="cs-widget-msg cs-${m.role}">
        <div class="cs-widget-msg-avatar">${m.role === 'user' ? '👤' : config.botAvatar}</div>
        <div class="cs-widget-msg-bubble">
          <div>${m.content}</div>
          <div class="cs-widget-msg-time">${m.time}</div>
        </div>
      </div>
    `).join('');
    body.scrollTop = body.scrollHeight;
  }

  function toggleWindow(open) {
    isOpen = typeof open === 'boolean' ? open : !isOpen;
    if (isOpen) {
      win.classList.add('cs-open');
      renderMessages();
      setTimeout(() => input.focus(), 300);
      ensureSession();
    } else {
      win.classList.remove('cs-open');
    }
  }

  btn.addEventListener('click', () => toggleWindow());
  closeBtn.addEventListener('click', () => toggleWindow(false));

  async function ensureSession() {
    if (sessionId) return sessionId;
    try {
      const res = await fetch(`${config.apiBase}/api/v1/chat/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': config.tenantId },
        body: JSON.stringify({
          customer_id: customerId,
          customer_name: '网页访客·' + customerId.substring(5),
          product_code: config.productCode
        })
      });
      if (res.ok) {
        const data = await res.json();
        sessionId = data.id;
      }
    } catch (e) {
      console.warn('[CS Widget] Failed to init session, fallback to direct query', e);
    }
    return sessionId;
  }

  async function sendMessage() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    sendBtn.disabled = true;

    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    messages.push({
      id: 'm_' + Date.now(),
      role: 'user',
      content: text,
      time: timeStr
    });
    renderMessages();

    // 思考中占位
    const typingId = 'typing_' + Date.now();
    messages.push({
      id: typingId,
      role: 'assistant',
      content: '<span class="cs-widget-typing"></span><span class="cs-widget-typing"></span><span class="cs-widget-typing"></span>',
      time: timeStr
    });
    renderMessages();

    try {
      await ensureSession();
      const res = await fetch(`${config.apiBase}/api/v1/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Tenant-Id': config.tenantId,
          'X-User-Product': config.productCode
        },
        body: JSON.stringify({
          customer_id: customerId,
          product_code: config.productCode,
          session_id: sessionId,
          query: text
        })
      });

      // 移除占位
      messages = messages.filter(m => m.id !== typingId);

      if (res.ok) {
        const data = await res.json();
        messages.push({
          id: 'bot_' + Date.now(),
          role: 'assistant',
          content: data.reply || data.answer || '已为您查询到相关信息。',
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });
      } else {
        messages.push({
          id: 'bot_' + Date.now(),
          role: 'assistant',
          content: '抱歉，当前客服网络繁忙，请稍后再试或联系人工客服。',
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });
      }
    } catch (e) {
      messages = messages.filter(m => m.id !== typingId);
      messages.push({
        id: 'bot_' + Date.now(),
        role: 'assistant',
        content: '未能连接到企业客服服务器，请检查网络连接。',
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      });
    } finally {
      sendBtn.disabled = false;
      renderMessages();
      input.focus();
    }
  }

  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendMessage();
  });

  // 全局暴露 API
  window.initCSWidget = function (customConf) {
    if (customConf) {
      Object.assign(config, customConf);
      if (customConf.primaryColor) {
        btn.style.background = customConf.primaryColor;
      }
      if (customConf.botAvatar) {
        document.getElementById('csWidgetBtnIcon').innerText = customConf.botAvatar;
      }
    }
    toggleWindow(true);
  };

  window.toggleCSWidget = toggleWindow;
})();
