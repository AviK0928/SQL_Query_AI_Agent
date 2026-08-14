/* SQL Query AI Agent - frontend behaviour.
 *
 * No framework, no build step. The server owns conversation state; this file
 * only holds the session id that points at it.
 */

const chat = document.getElementById("chat");
const input = document.getElementById("question");
const sendButton = document.getElementById("send");
const schemaContent = document.getElementById("schema-content");

/* Plain variable, deliberately not persisted (see D12 in the README).
 * Server-side sessions do not survive a restart, so storing this id across a
 * page refresh would only produce a reference to a session that may be gone. */
let sessionId = null;
let busy = false;

/* --- rendering ---------------------------------------------------- */

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function addMessage(role) {
  const wrapper = element("div", `message ${role}`);
  const bubble = element("div", "bubble");
  wrapper.appendChild(bubble);
  chat.appendChild(wrapper);
  chat.scrollIntoView({ block: "end", behavior: "smooth" });
  return bubble;
}

function buildTable(columns, rows, truncated) {
  const wrap = element("div", "results");
  const table = document.createElement("table");

  const headRow = document.createElement("tr");
  columns.forEach(name => headRow.appendChild(element("th", null, name)));
  table.appendChild(headRow);

  rows.slice(0, 50).forEach(row => {
    const tr = document.createElement("tr");
    row.forEach(value => {
      tr.appendChild(element("td", null, value === null ? "-" : String(value)));
    });
    table.appendChild(tr);
  });

  const shown = Math.min(rows.length, 50);
  let note = `${shown} of ${rows.length} row${rows.length === 1 ? "" : "s"}`;
  if (truncated) note += " (result set was capped at 200 by the server)";
  table.appendChild(element("caption", null, note));

  wrap.appendChild(table);
  return wrap;
}

function buildSqlBlock(sql) {
  const details = element("details", "sql");
  details.appendChild(element("summary", null, "Show generated SQL"));
  const pre = document.createElement("pre");
  pre.appendChild(element("code", null, sql));
  details.appendChild(pre);
  return details;
}

/* textContent everywhere above means model output and database values are
 * inserted as text, never parsed as HTML. innerHTML here would be an XSS
 * hole: the model's reply is untrusted output, exactly like its SQL. */

function renderAnswer(bubble, data) {
  bubble.textContent = "";

  if (data.error && !data.sql) {
    bubble.classList.add("error");
  }

  bubble.appendChild(element("p", null, data.answer));

  if (data.sql) bubble.appendChild(buildSqlBlock(data.sql));
  if (data.columns && data.columns.length && data.rows && data.rows.length) {
    bubble.appendChild(buildTable(data.columns, data.rows, data.truncated));
  }

  chat.scrollIntoView({ block: "end", behavior: "smooth" });
}

/* --- sending ------------------------------------------------------ */

async function send(question) {
  if (busy) return;
  const text = question.trim();
  if (!text) return;

  busy = true;
  sendButton.disabled = true;
  input.value = "";

  addMessage("user").appendChild(element("p", null, text));

  const pending = addMessage("agent");
  const dots = element("div", "dots");
  dots.append(element("span"), element("span"), element("span"));
  pending.appendChild(dots);

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, session_id: sessionId }),
    });

    if (!response.ok) {
      const detail = response.status === 422
        ? "That question was rejected - it may be empty or too long."
        : `The server returned an error (${response.status}).`;
      pending.classList.add("error");
      pending.textContent = detail;
      return;
    }

    const data = await response.json();
    sessionId = data.session_id;
    renderAnswer(pending, data);

  } catch (err) {
    pending.classList.add("error");
    pending.textContent =
      "Could not reach the server. If it has been idle it may be waking up - try again in a minute.";
  } finally {
    busy = false;
    sendButton.disabled = false;
    input.focus();
  }
}

/* --- events ------------------------------------------------------- */

sendButton.addEventListener("click", () => send(input.value));

input.addEventListener("keydown", event => {
  if (event.key === "Enter") send(input.value);
});

document.addEventListener("click", event => {
  if (event.target.classList.contains("example")) {
    send(event.target.textContent);
  }
});

/* --- schema panel ------------------------------------------------- */

fetch("/schema")
  .then(r => r.json())
  .then(data => {
    schemaContent.textContent = data.tables
      .map(t => `${t.name}(${t.columns.map(c => c.name).join(", ")})`)
      .join("\n");
  })
  .catch(() => {
    schemaContent.textContent = "Could not load the schema.";
  });
