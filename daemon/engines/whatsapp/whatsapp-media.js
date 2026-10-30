/**
 * whatsapp-media.js -- how a file goes OUT on WhatsApp.
 *
 * The one builder for an outbound Baileys content object, used by the guarded
 * send route. Kind and MIME come from `file-types.js` (ported from an earlier prototype).
 *
 * Inline only for formats WhatsApp renders inline; everything else is a
 * document, which always arrives. Audio carries no caption on WhatsApp, so a
 * captioned audio file is sent as a document rather than losing the caption.
 */
import { kindOf, mimeOf } from './file-types.js';

const INLINE_IMAGE = new Set(['image/jpeg', 'image/png', 'image/webp']);
const INLINE_VIDEO = new Set(['video/mp4']);
const INLINE_AUDIO = new Set(['audio/mpeg', 'audio/ogg', 'audio/mp4', 'audio/aac']);

/**
 * @param {{ buffer: Buffer, fileName: string, caption?: string, text?: string }} input
 * @returns {object} Baileys `sendMessage` content
 */
export function buildOutboundContent({ buffer, fileName, caption, text }) {
  if (!fileName) throw new Error('buildOutboundContent: fileName is required');
  if (!buffer || buffer.length === 0) throw new Error(`buildOutboundContent: ${fileName} is empty`);
  const mimetype = mimeOf(fileName);
  const kind = kindOf(fileName);
  const cap = caption || text || undefined;
  const withCaption = (obj) => (cap ? { ...obj, caption: cap } : obj);

  if (kind === 'image' && INLINE_IMAGE.has(mimetype)) return withCaption({ image: buffer, mimetype });
  if (kind === 'video' && INLINE_VIDEO.has(mimetype)) return withCaption({ video: buffer, mimetype });
  if (kind === 'audio' && INLINE_AUDIO.has(mimetype) && !cap) return { audio: buffer, mimetype };
  return withCaption({ document: buffer, mimetype, fileName });
}

/** The OGG container's capture pattern: every page starts with it. */
const OGG_MAGIC = Buffer.from('OggS');

/**
 * A push-to-talk voice note (MS-18a, AC-539): OGG/Opus, which WhatsApp plays
 * inline with a waveform. The daemon encodes it (ffmpeg); this checks the
 * container by its bytes, because anything else would arrive as a voice note
 * nobody can play.
 * @param {Buffer} buffer
 */
export function buildVoiceNote(buffer) {
  if (!buffer || buffer.length < OGG_MAGIC.length || !buffer.subarray(0, OGG_MAGIC.length).equals(OGG_MAGIC)) {
    throw new Error('a voice note must be an OGG/Opus file');
  }
  return { audio: buffer, mimetype: 'audio/ogg; codecs=opus', ptt: true };
}
