// The live-session connection. Wire protocol is untouched from the original console: connect only
// once an intervention is claimed, send {kind,x,y,key,text} gestures, receive {status,message,frame}
// or {error} back. This module only adds a typed pub/sub layer over that same exchange so more than
// one page (Interventions, and Overview's status strip) can observe it without owning the socket.
//
// Deliberately NOT tied to the router: navigating away must not drop operator control mid-session,
// so the connection is a module-level singleton, opened by claim() and closed by release() exactly
// as the original inline console did.

const listeners = new Set();
let socket = null;

function emit(event) {
  for (const fn of listeners) fn(event);
}

export function onLive(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isConnected() {
  return socket !== null && socket.readyState === WebSocket.OPEN;
}

export function connectLive() {
  if (socket) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${proto}//${location.host}/ws`);
  socket.onopen = () => emit({ type: "open" });
  socket.onclose = () => {
    socket = null;
    emit({ type: "close" });
  };
  socket.onerror = () => emit({ type: "error" });
  socket.onmessage = (evt) => {
    let data;
    try {
      data = JSON.parse(evt.data);
    } catch {
      return;
    }
    if (data.error) {
      emit({ type: "refused", error: data.error });
      return;
    }
    emit({
      type: "frame",
      status: data.status,
      message: data.message,
      frame: data.frame, // base64 PNG, already produced by the driver's screenshot() after each gesture
    });
  };
}

export function disconnectLive() {
  if (socket) {
    socket.close();
    socket = null;
  }
}

function send(gesture) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(gesture));
  return true;
}

export const gesture = {
  click: (x, y) => send({ kind: "mouse_click", x: Math.round(x), y: Math.round(y) }),
  move: (x, y) => send({ kind: "mouse_move", x: Math.round(x), y: Math.round(y) }),
  key: (key) => send({ kind: "key", key }),
  text: (text) => send({ kind: "text", text }),
};
