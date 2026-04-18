import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import qrcode from "qrcode-terminal";
import pino from "pino";
import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  normalizeMessageContent,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";

const logger = pino({ level: process.env.LOG_LEVEL || "info" });

const config = {
  ingestUrl: process.env.SENTINEL_INGEST_URL || "http://127.0.0.1:8080/ingest",
  authToken: process.env.SENTINEL_AUTH_TOKEN || "",
  platform: process.env.SENTINEL_PLATFORM || "whatsapp",
  authDir: process.env.SENTINEL_BAILEYS_AUTH_DIR || "./.baileys-auth",
  mediaDir: process.env.SENTINEL_MEDIA_DIR || "./.media",
  groupsOnly: process.env.SENTINEL_GROUPS_ONLY !== "false",
  ignoreFromMe: process.env.SENTINEL_IGNORE_FROM_ME !== "false",
};

await fs.mkdir(config.authDir, { recursive: true });
await fs.mkdir(config.mediaDir, { recursive: true });

const state = await useMultiFileAuthState(config.authDir);

function extensionForMime(mimeType, fallback = ".bin") {
  if (!mimeType) return fallback;
  if (mimeType.includes("ogg")) return ".ogg";
  if (mimeType.includes("mpeg")) return ".mp3";
  if (mimeType.includes("mp4")) return ".mp4";
  if (mimeType.includes("wav")) return ".wav";
  if (mimeType.includes("webm")) return ".webm";
  return fallback;
}

function extractText(messageContent) {
  if (!messageContent) return null;
  if (messageContent.conversation) return messageContent.conversation;
  if (messageContent.extendedTextMessage?.text) return messageContent.extendedTextMessage.text;
  if (messageContent.imageMessage?.caption) return messageContent.imageMessage.caption;
  if (messageContent.videoMessage?.caption) return messageContent.videoMessage.caption;
  if (messageContent.documentMessage?.caption) return messageContent.documentMessage.caption;
  return null;
}

function extractContextInfo(messageContent) {
  return (
    messageContent?.extendedTextMessage?.contextInfo ||
    messageContent?.imageMessage?.contextInfo ||
    messageContent?.videoMessage?.contextInfo ||
    messageContent?.audioMessage?.contextInfo ||
    messageContent?.documentMessage?.contextInfo ||
    null
  );
}

async function persistAudio(message, sock) {
  const normalizedMessage = normalizeMessageContent(message.message);
  const audioMessage = normalizedMessage?.audioMessage;
  if (!audioMessage) return { mediaPath: null, mediaType: null, metadata: {} };
  const buffer = await downloadMediaMessage(
    message,
    "buffer",
    {},
    { logger, reuploadRequest: sock.updateMediaMessage }
  );
  const extension = extensionForMime(audioMessage.mimetype, ".ogg");
  const fileName = `${message.key.id}${extension}`;
  const absolutePath = path.resolve(config.mediaDir, fileName);
  await fs.writeFile(absolutePath, buffer);
  return {
    mediaPath: absolutePath,
    mediaType: audioMessage.mimetype || "audio/ogg",
    metadata: {
      duration_seconds: audioMessage.seconds ?? null,
      ptt: Boolean(audioMessage.ptt),
      mimetype: audioMessage.mimetype || null,
    },
  };
}

async function postJson(url, payload) {
  const headers = { "Content-Type": "application/json" };
  if (config.authToken) {
    headers.Authorization = `Bearer ${config.authToken}`;
  }
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Sentinel ingest failed: HTTP ${response.status} ${text}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function shouldIgnoreMessage(message) {
  if (!message?.message || !message.key?.remoteJid) return true;
  if (config.groupsOnly && !message.key.remoteJid.endsWith("@g.us")) return true;
  if (config.ignoreFromMe && message.key.fromMe) return true;
  return false;
}

async function handleMessage(message, sock) {
  if (shouldIgnoreMessage(message)) return;
  const normalizedContent = normalizeMessageContent(message.message);
  if (!normalizedContent) return;
  const remoteJid = message.key.remoteJid;
  const participant = message.key.participant || remoteJid;
  const contextInfo = extractContextInfo(normalizedContent);
  const audioInfo = normalizedContent.audioMessage ? await persistAudio(message, sock) : null;
  const messageType = normalizedContent.audioMessage ? "audio" : "text";
  const rawText = extractText(normalizedContent);
  if (!rawText && messageType !== "audio") return;
  const sentAtSeconds = Number(message.messageTimestamp || 0);
  const sentAt = sentAtSeconds
    ? new Date(sentAtSeconds * 1000).toISOString()
    : new Date().toISOString();

  const payload = {
    platform: config.platform,
    external_group_id: remoteJid,
    group_name: remoteJid,
    external_user_id: participant,
    user_name: message.pushName || participant,
    message_type: messageType,
    raw_text: rawText,
    language: "pt-BR",
    external_message_id: message.key.id,
    received_at: new Date().toISOString(),
    sent_at: sentAt,
    reply_to_message_id: contextInfo?.stanzaId || null,
    quoted_message_id: contextInfo?.stanzaId || null,
    has_media: Boolean(audioInfo?.mediaPath),
    media_type: audioInfo?.mediaType || null,
    media_path: audioInfo?.mediaPath || null,
    metadata: {
      source: "baileys-sidecar",
      remote_jid: remoteJid,
      participant,
      ...audioInfo?.metadata,
    },
  };
  const result = await postJson(config.ingestUrl, payload);
  logger.info(
    {
      remoteJid,
      messageId: message.key.id,
      severity: result.severity,
      riskScore: result.risk_score,
      alerts: result.alert_ids,
    },
    "message_ingested"
  );
}

async function startSocket() {
  const sock = makeWASocket({
    auth: state.state,
    logger,
    markOnlineOnConnect: false,
    syncFullHistory: false,
  });

  sock.ev.on("creds.update", state.saveCreds);

  sock.ev.on("connection.update", async (update) => {
    if (update.qr) {
      qrcode.generate(update.qr, { small: true });
    }
    if (update.connection === "open") {
      logger.info("baileys_connected");
    }
    if (update.connection === "close") {
      const statusCode = update.lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      logger.warn({ statusCode, shouldReconnect }, "baileys_connection_closed");
      if (shouldReconnect) {
        setTimeout(() => {
          startSocket().catch((error) => logger.error({ error }, "baileys_restart_failed"));
        }, 1000);
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const message of messages) {
      try {
        await handleMessage(message, sock);
      } catch (error) {
        logger.error({ error, key: message?.key }, "message_ingest_failed");
      }
    }
  });
}

startSocket().catch((error) => {
  logger.error({ error }, "baileys_start_failed");
  process.exitCode = 1;
});
