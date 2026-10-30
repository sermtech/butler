/**
 * whatsapp-media.js#buildOutboundContent -- the ONE place that decides how a
 * file goes out on WhatsApp (ported from an earlier prototype's whatsapp-media test, AC-515).
 * Inline only where WhatsApp renders the format inline; everything else is a
 * document, so it always arrives.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { buildOutboundContent } from '../whatsapp-media.js';

const buf = Buffer.from('x');

test('jpg/png/webp go inline as images with the caption', () => {
  for (const [name, mime] of [['a.jpg', 'image/jpeg'], ['a.png', 'image/png'], ['a.webp', 'image/webp']]) {
    const c = buildOutboundContent({ buffer: buf, fileName: name, caption: 'look' });
    assert.deepEqual(c, { image: buf, mimetype: mime, caption: 'look' }, name);
  }
});

test('mp4 goes inline as a video', () => {
  assert.deepEqual(buildOutboundContent({ buffer: buf, fileName: 'clip.mp4', caption: 'hi' }),
    { video: buf, mimetype: 'video/mp4', caption: 'hi' });
});

test('mp3/ogg/m4a go inline as audio when there is no caption', () => {
  assert.deepEqual(buildOutboundContent({ buffer: buf, fileName: 'a.mp3' }), { audio: buf, mimetype: 'audio/mpeg' });
  assert.equal(buildOutboundContent({ buffer: buf, fileName: 'a.ogg' }).mimetype, 'audio/ogg');
});

test('audio WITH a caption becomes a document, because WhatsApp audio has no caption', () => {
  const c = buildOutboundContent({ buffer: buf, fileName: 'talk.mp3', caption: 'the lecture' });
  assert.deepEqual(c, { document: buf, mimetype: 'audio/mpeg', fileName: 'talk.mp3', caption: 'the lecture' });
});

test('pdf, docx, zip, gif, mov and unknown types go as documents with the right MIME', () => {
  const cases = {
    'r.pdf': 'application/pdf',
    'b.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'z.zip': 'application/zip',
    'g.gif': 'image/gif',
    'm.mov': 'video/quicktime',
    'thing.qqq': 'application/octet-stream',
  };
  for (const [name, mime] of Object.entries(cases)) {
    const c = buildOutboundContent({ buffer: buf, fileName: name });
    assert.deepEqual(c, { document: buf, mimetype: mime, fileName: name }, name);
  }
});

test('text is the caption when no caption is given', () => {
  assert.equal(buildOutboundContent({ buffer: buf, fileName: 'a.pdf', text: 'here it is' }).caption, 'here it is');
});

test('an empty buffer or a missing name is a caller bug and throws', () => {
  assert.throws(() => buildOutboundContent({ buffer: Buffer.alloc(0), fileName: 'a.pdf' }), /empty/);
  assert.throws(() => buildOutboundContent({ buffer: buf, fileName: '' }), /fileName/);
});
