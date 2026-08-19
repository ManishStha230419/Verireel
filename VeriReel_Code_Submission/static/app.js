'use strict';

const ALLOWED_EXTENSIONS = new Set(['mp4', 'mov', 'm4v', 'avi', 'webm', 'mkv']);
const MAX_FILE_BYTES = 200 * 1024 * 1024;
const POLL_DELAY_MS = 900;

const state = {
  jobId: null,
  jobToken: null,
  pollTimer: null,
  polling: false,
  consecutivePollErrors: 0,
  inputMode: 'upload',
};

const byId = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', () => {
  const form = byId('analysisForm');
  const threshold = byId('threshold');

  form.addEventListener('submit', startAnalysis);
  byId('resetButton').addEventListener('click', () => resetAnalyzer());
  byId('cancelButton').addEventListener('click', cancelAnalysis);
  byId('downloadReport').addEventListener('click', downloadReport);
  document.querySelectorAll('.mode-button').forEach((button) => {
    button.addEventListener('click', () => setInputMode(button.dataset.mode));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const nextMode = state.inputMode === 'upload' ? 'tiktok' : 'upload';
      setInputMode(nextMode);
      document.querySelector(`.mode-button[data-mode="${nextMode}"]`).focus();
    });
  });
  threshold.addEventListener('input', () => {
    byId('thresholdValue').textContent = `${threshold.value}%`;
  });

  setupFileInput(1);
  setupFileInput(2);
  setInputMode('upload');
  setupMotion();
});

function setupMotion() {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const revealItems = [...document.querySelectorAll('.reveal')];

  revealItems.forEach((item, index) => {
    item.style.transitionDelay = reducedMotion ? '0ms' : `${Math.min(index % 4, 3) * 70}ms`;
  });

  if (reducedMotion || !('IntersectionObserver' in window)) {
    revealItems.forEach((item) => item.classList.add('is-visible'));
  } else {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -9% 0px', threshold: 0.08 });
    revealItems.forEach((item) => observer.observe(item));
  }

  const meter = byId('scrollMeter');
  let meterFrame = 0;
  const updateMeter = () => {
    meterFrame = 0;
    const scrollable = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    meter.style.width = `${Math.min(100, Math.max(0, (window.scrollY / scrollable) * 100))}%`;
  };
  window.addEventListener('scroll', () => {
    if (meterFrame) return;
    meterFrame = window.requestAnimationFrame(updateMeter);
  }, { passive: true });
  updateMeter();

  if (!reducedMotion && window.matchMedia('(pointer: fine)').matches) {
    let pointerFrame = 0;
    let pointerX = window.innerWidth / 2;
    let pointerY = window.innerHeight * 0.08;
    const updatePointerGlow = () => {
      pointerFrame = 0;
      document.documentElement.style.setProperty('--cursor-x', `${pointerX}px`);
      document.documentElement.style.setProperty('--cursor-y', `${pointerY}px`);
    };
    window.addEventListener('pointermove', (event) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      if (!pointerFrame) pointerFrame = window.requestAnimationFrame(updatePointerGlow);
    }, { passive: true });

    document.querySelectorAll('[data-ambient-panel]').forEach((panel) => {
      panel.addEventListener('pointermove', (event) => {
        const bounds = panel.getBoundingClientRect();
        const localX = ((event.clientX - bounds.left) / Math.max(1, bounds.width)) * 100;
        const localY = ((event.clientY - bounds.top) / Math.max(1, bounds.height)) * 100;
        panel.style.setProperty('--ambient-x', `${Math.max(0, Math.min(100, localX))}%`);
        panel.style.setProperty('--ambient-y', `${Math.max(0, Math.min(100, localY))}%`);
      }, { passive: true });
    });
  }
}

function setInputMode(mode) {
  state.inputMode = mode === 'tiktok' ? 'tiktok' : 'upload';
  const isTikTok = state.inputMode === 'tiktok';
  byId('uploadModeFields').classList.toggle('hidden', isTikTok);
  byId('tiktokModeFields').classList.toggle('hidden', !isTikTok);
  byId('uploadModeFields').setAttribute('aria-hidden', String(isTikTok));
  byId('tiktokModeFields').setAttribute('aria-hidden', String(!isTikTok));

  document.querySelectorAll('.mode-button').forEach((button) => {
    const selected = button.dataset.mode === state.inputMode;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  });

  byId('retentionNote').textContent = isTikTok
    ? 'Public posts are downloaded only for this job. The temporary video files are deleted when analysis ends.'
    : 'Source files are processed locally by this server and deleted when the job ends.';
  byId('analyzeButton').querySelector('span').textContent = isTikTok
    ? 'Analyze TikTok links'
    : 'Run fingerprint analysis';
  configureProgressSteps(state.inputMode);
  clearError();
}

function configureProgressSteps(mode) {
  const steps = mode === 'tiktok'
    ? [[3, 'Links accepted'], [8, 'Download TikTok posts'], [40, 'Fingerprint videos'], [78, 'Compare signals'], [92, 'Build review report']]
    : [[5, 'Files received'], [15, 'Fingerprint video 1'], [45, 'Fingerprint video 2'], [72, 'Compare signals'], [90, 'Build review report']];
  document.querySelectorAll('#progressSteps li').forEach((item, index) => {
    item.dataset.point = String(steps[index][0]);
    item.textContent = steps[index][1];
    item.classList.remove('active', 'done');
  });
}

function setupFileInput(number) {
  const input = byId(`video${number}`);
  const card = byId(`drop${number}`);
  input.addEventListener('change', () => updateFileCard(number));

  ['dragenter', 'dragover'].forEach((eventName) => {
    card.addEventListener(eventName, (event) => {
      event.preventDefault();
      card.classList.add('dragging');
    });
  });
  ['dragleave', 'drop'].forEach((eventName) => {
    card.addEventListener(eventName, (event) => {
      event.preventDefault();
      card.classList.remove('dragging');
    });
  });
  card.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      updateFileCard(number);
    } catch (_) {
      showError('Drag-and-drop is not available in this browser. Use Browse files instead.');
    }
  });
}

function updateFileCard(number) {
  const input = byId(`video${number}`);
  const file = input.files?.[0];
  const card = byId(`drop${number}`);
  const name = byId(`fileName${number}`);
  const metadata = byId(`fileMeta${number}`);

  card.classList.toggle('has-file', Boolean(file));
  if (!file) {
    name.textContent = 'Choose a video';
    metadata.textContent = 'or drag and drop it here';
    return;
  }
  name.textContent = file.name;
  metadata.textContent = `${formatBytes(file.size)} · ${file.type || 'video file'}`;
  if (!byId(`label${number}`).value) {
    byId(`label${number}`).placeholder = file.name.replace(/\.[^.]+$/, '');
  }
  clearError();
}

async function startAnalysis(event) {
  event.preventDefault();
  clearError();

  const isTikTok = state.inputMode === 'tiktok';
  const validationError = isTikTok
    ? validateTikTokLinks(byId('tiktokUrl1').value, byId('tiktokUrl2').value)
    : validateFiles(byId('video1').files?.[0], byId('video2').files?.[0]);
  if (validationError) {
    showError(validationError);
    return;
  }
  if (!byId('acknowledge').checked) {
    showError('Confirm that you understand the human-review requirement before continuing.');
    byId('acknowledge').focus();
    return;
  }

  const button = byId('analyzeButton');
  button.disabled = true;
  byId('analysisForm').classList.add('hidden');
  byId('results').classList.add('hidden');
  byId('progressPanel').classList.remove('hidden');
  setProgress(2, isTikTok ? 'Submitting two TikTok links securely.' : 'Uploading the two videos securely.');

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120000);
  try {
    const requestOptions = isTikTok
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-VeriReel-Request': '1' },
          body: JSON.stringify({
            url1: byId('tiktokUrl1').value.trim(),
            url2: byId('tiktokUrl2').value.trim(),
            threshold: byId('threshold').value,
          }),
          signal: controller.signal,
        }
      : {
          method: 'POST',
          headers: { 'X-VeriReel-Request': '1' },
          body: new FormData(event.currentTarget),
          signal: controller.signal,
        };
    const response = await fetch('/api/analyze', requestOptions);
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.error || 'The server could not start the analysis.');

    state.jobId = payload.job_id;
    state.jobToken = payload.access_token;
    if (!state.jobId || !state.jobToken) throw new Error('The server did not create a protected analysis session.');
    state.polling = true;
    state.consecutivePollErrors = 0;
    schedulePoll(250);
  } catch (error) {
    const message = error.name === 'AbortError'
      ? 'The upload took too long. Try smaller files or a faster connection.'
      : error.message;
    failAnalysis(message);
  } finally {
    window.clearTimeout(timeout);
  }
}

function validateTikTokLinks(url1, url2) {
  for (const [index, value] of [url1, url2].entries()) {
    const raw = String(value || '').trim();
    if (!raw) return 'Paste both TikTok video links before starting the analysis.';
    try {
      const parsed = new URL(raw);
      const hostname = parsed.hostname.toLowerCase().replace(/\.$/, '');
      const isTikTok = hostname === 'tiktok.com' || hostname.endsWith('.tiktok.com');
      if (parsed.protocol !== 'https:' || !isTikTok || parsed.pathname === '/') throw new Error('invalid');
    } catch (_) {
      return `TikTok ${index + 1} must be a valid HTTPS video link from tiktok.com.`;
    }
  }
  return '';
}

function validateFiles(file1, file2) {
  if (!file1 || !file2) return 'Choose both video files before starting the analysis.';
  for (const [index, file] of [file1, file2].entries()) {
    const extension = file.name.split('.').pop()?.toLowerCase() || '';
    if (!ALLOWED_EXTENSIONS.has(extension)) {
      return `Video ${index + 1} has an unsupported format. Use MP4, MOV, M4V, AVI, WebM, or MKV.`;
    }
    if (file.size <= 0) return `Video ${index + 1} is empty.`;
    if (file.size > MAX_FILE_BYTES) return `Video ${index + 1} is larger than 200 MB.`;
  }
  return '';
}

function schedulePoll(delay = POLL_DELAY_MS) {
  window.clearTimeout(state.pollTimer);
  if (!state.polling || !state.jobId) return;
  state.pollTimer = window.setTimeout(pollStatus, delay);
}

async function pollStatus() {
  if (!state.polling || !state.jobId) return;
  try {
    const response = await fetch(`/api/status/${encodeURIComponent(state.jobId)}`, {
      headers: { Accept: 'application/json', 'X-Job-Token': state.jobToken },
      cache: 'no-store',
    });
    const job = await readJson(response);
    if (response.status === 404) {
      state.polling = false;
      failAnalysis('This analysis session was lost because the server restarted or more than one local server was running. Start VeriReel once, then retry the comparison.');
      return;
    }
    if (!response.ok) throw new Error(job.error || 'Could not read the analysis status.');
    state.consecutivePollErrors = 0;

    if (['queued', 'processing', 'cancelling'].includes(job.status)) {
      setProgress(job.progress || 0, job.message || 'Processing videos.');
      schedulePoll();
      return;
    }
    if (job.status === 'complete') {
      state.polling = false;
      setProgress(100, job.message || 'Analysis complete.');
      window.setTimeout(() => renderResults(job.result), 250);
      return;
    }
    if (job.status === 'cancelled') {
      state.polling = false;
      resetAnalyzer('Analysis cancelled. Temporary source videos were deleted.');
      return;
    }
    if (job.status === 'error') {
      state.polling = false;
      failAnalysis(job.message || 'The analysis failed.');
      return;
    }
    throw new Error('The server returned an unknown analysis status.');
  } catch (error) {
    state.consecutivePollErrors += 1;
    if (state.consecutivePollErrors < 5) {
      setProgress(Number(byId('progressTrack').getAttribute('aria-valuenow') || 0), 'Connection interrupted. Retrying status check.');
      schedulePoll(1500);
      return;
    }
    failAnalysis(error.message);
  }
}

function setProgress(value, message) {
  const progress = Math.max(0, Math.min(100, Number(value) || 0));
  byId('progressValue').textContent = `${Math.round(progress)}%`;
  byId('progressMessage').textContent = message;
  byId('progressBar').style.width = `${progress}%`;
  byId('progressTrack').setAttribute('aria-valuenow', String(Math.round(progress)));

  const items = [...document.querySelectorAll('#progressSteps li')];
  items.forEach((item, index) => {
    const point = Number(item.dataset.point);
    const nextPoint = index + 1 < items.length ? Number(items[index + 1].dataset.point) : 100;
    item.classList.toggle('done', progress >= nextPoint || progress === 100);
    item.classList.toggle('active', progress >= point && progress < nextPoint);
  });
}

async function cancelAnalysis() {
  if (!state.jobId) {
    resetAnalyzer();
    return;
  }
  byId('cancelButton').disabled = true;
  state.polling = false;
  window.clearTimeout(state.pollTimer);
  try {
    await fetch(`/api/status/${encodeURIComponent(state.jobId)}`, {
      method: 'DELETE',
      headers: { 'X-Job-Token': state.jobToken, 'X-VeriReel-Request': '1' },
    });
  } finally {
    resetAnalyzer('Analysis cancellation requested. Temporary media will be removed after the current step.');
  }
}

function renderResults(result) {
  const { similarity, video1, video2, report } = result;
  byId('progressPanel').classList.add('hidden');
  byId('analyzer').classList.add('hidden');
  byId('results').classList.remove('hidden');

  renderOutcome(similarity, report);
  renderMetrics(similarity);
  renderAlignment(similarity);
  renderVideos(video1, video2, report.original_video);
  renderAnalysis(report.analysis || '');
  renderActions(report.action_steps || []);
  renderLimitations(report);

  const link = byId('downloadReport');
  link.href = '#';
  link.classList.remove('disabled');
  link.removeAttribute('aria-disabled');

  byId('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderOutcome(similarity, report) {
  const score = Number(similarity.overall || 0);
  const card = byId('outcomeCard');
  card.className = `outcome-card ${report.severity || 'low'}`;
  byId('overallScore').textContent = `${score.toFixed(1)}%`;
  byId('scoreRing').style.setProperty('--score', `${score * 3.6}deg`);
  byId('outcomeLabel').textContent = report.confidence_band || 'Review outcome';
  byId('outcomeTitle').textContent = report.verdict_text || 'Review result';
  byId('outcomeSummary').textContent = report.human_review_required
    ? 'Review the aligned evidence and rights context before taking any action.'
    : 'The selected threshold was not reached; no escalation is recommended from this result alone.';
  byId('resultThreshold').textContent = `${Number(report.decision_threshold).toFixed(0)}%`;
}

function renderMetrics(similarity) {
  const metrics = [
    ['Visual structure', 'Baseline-adjusted frame hashes', similarity.perceptual, '45% base weight'],
    ['Editing rhythm', 'Aligned frame-to-frame change', similarity.temporal, '25% base weight'],
    ['Colour profile', 'Aligned colour distribution', similarity.color, '20% base weight'],
    ['Movement pattern', 'Aligned frame-difference motion', similarity.motion, '10% base weight'],
    ['Evidence gate', 'Supporting evidence retained', similarity.support_gate, 'Anti-false-positive control'],
    ['pHash / wHash', 'Baseline-adjusted diagnostics', average(similarity.phash, similarity.whash), `${formatScore(similarity.phash)} / ${formatScore(similarity.whash)}`],
    ['dHash / aHash', 'Baseline-adjusted diagnostics', average(similarity.dhash, similarity.ahash), `${formatScore(similarity.dhash)} / ${formatScore(similarity.ahash)}`],
  ];
  byId('metricList').innerHTML = metrics.map(([name, description, score, note]) => `
    <div class="metric-row">
      <div class="metric-head">
        <span class="metric-name">${escapeHtml(name)}<small>${escapeHtml(description)}</small></span>
        <span class="metric-value" title="${escapeHtml(note)}">${formatScore(score)}</span>
      </div>
      <div class="metric-track"><span data-width="${clampScore(score)}%"></span></div>
    </div>`).join('');

  window.requestAnimationFrame(() => {
    document.querySelectorAll('.metric-track span[data-width]').forEach((bar) => {
      bar.style.width = bar.dataset.width;
    });
  });
}

function renderAlignment(similarity) {
  const alignment = similarity.alignment || {};
  const orientation = alignment.orientation === 'mirrored' ? 'Mirrored candidate' : 'Normal orientation';
  const scale = Number(alignment.time_scale || 1);
  const offset = Number(alignment.offset_frames || 0);
  const matched = Number(alignment.matched_frames || 0);
  const coverage = Number(alignment.longer_video_coverage || 0);
  byId('alignmentList').innerHTML = `
    <dt>Orientation</dt><dd>${escapeHtml(orientation)}</dd>
    <dt>Estimated time scale</dt><dd>${scale.toFixed(2)}x</dd>
    <dt>Best window offset</dt><dd>${Math.round(offset)} sampled frame${Math.round(offset) === 1 ? '' : 's'}</dd>
    <dt>Matched frames</dt><dd>${Math.round(matched)}</dd>
    <dt>Longer-video coverage</dt><dd>${formatScore(coverage)}</dd>
    <dt>Duration ratio</dt><dd>${formatScore(similarity.duration)}</dd>`;
}

function renderVideos(video1, video2, original) {
  const earlierText = original === 'video1'
    ? 'Video 1 has the earlier supplied date'
    : original === 'video2'
      ? 'Video 2 has the earlier supplied date'
      : 'Publication order not inferred';
  byId('earlierNote').textContent = earlierText;

  byId('videoResults').innerHTML = [video1, video2].map((video, index) => {
    const resolution = video.resolution || {};
    const source = video.platform || (video.source_type === 'tiktok_url' ? 'TikTok' : 'Local upload');
    const creator = video.author || 'Not available';
    const popularity = video.view_count == null ? 'Not available' : `${formatCount(video.view_count)} views`;
    return `
      <article class="video-result">
        <div class="video-result-head"><h4 title="${escapeHtml(video.title)}">${escapeHtml(video.title || `Video ${index + 1}`)}</h4><span>VIDEO 0${index + 1}</span></div>
        <dl class="video-data">
          <div><dt>Duration</dt><dd>${formatDuration(video.duration)}</dd></div>
          <div><dt>Resolution</dt><dd>${Number(resolution.width || 0)}×${Number(resolution.height || 0)}</dd></div>
          <div><dt>Codec</dt><dd>${escapeHtml(video.codec || 'unknown')}</dd></div>
          <div><dt>Sampled</dt><dd>${Number(video.sampled_frames || 0)} frames</dd></div>
          <div><dt>Creator</dt><dd title="${escapeHtml(creator)}">${escapeHtml(creator)}</dd></div>
          <div><dt>Published</dt><dd>${escapeHtml(video.upload_date || 'Not provided')}</dd></div>
          <div><dt>Source</dt><dd>${escapeHtml(source)}</dd></div>
          <div><dt>Retention</dt><dd>Deleted</dd></div>
          <div><dt>SHA-256</dt><dd title="${escapeHtml(video.sha256 || 'Not available')}">${escapeHtml(shortDigest(video.sha256))}</dd></div>
          <div><dt>Container check</dt><dd>${escapeHtml(video.detected_container || 'Not recorded')}</dd></div>
          ${video.source_type === 'tiktok_url' ? `<div><dt>Public views</dt><dd>${escapeHtml(popularity)}</dd></div>` : `<div><dt>Frame rate</dt><dd>${Number(video.fps || 0).toFixed(2)} fps</dd></div>`}
        </dl>
      </article>`;
  }).join('');
}

function renderAnalysis(text) {
  const container = byId('analysisCopy');
  container.replaceChildren();
  text.split(/\n\s*\n/).filter(Boolean).forEach((paragraph) => {
    const element = document.createElement('p');
    element.textContent = paragraph;
    container.appendChild(element);
  });
}

function renderActions(actions) {
  byId('actionList').innerHTML = actions.map((action) => `
    <li><strong>${escapeHtml(action.action)}</strong><p>${escapeHtml(action.description)}</p></li>`).join('');
}

function renderLimitations(report) {
  byId('limitationsList').innerHTML = (report.limitations || [])
    .map((limitation) => `<li>${escapeHtml(limitation)}</li>`)
    .join('');
  byId('legalNotice').textContent = report.legal_notice || '';
}

function failAnalysis(message) {
  state.polling = false;
  window.clearTimeout(state.pollTimer);
  byId('progressPanel').classList.add('hidden');
  byId('analyzer').classList.remove('hidden');
  byId('analysisForm').classList.remove('hidden');
  byId('analyzeButton').disabled = false;
  showError(message || 'The analysis could not be completed.');
  byId('analyzer').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resetAnalyzer(notice = '') {
  const safeNotice = typeof notice === 'string' ? notice : '';
  state.polling = false;
  state.jobId = null;
  state.jobToken = null;
  state.consecutivePollErrors = 0;
  window.clearTimeout(state.pollTimer);

  byId('analysisForm').reset();
  byId('analyzer').classList.remove('hidden');
  byId('analysisForm').classList.remove('hidden');
  byId('progressPanel').classList.add('hidden');
  byId('results').classList.add('hidden');
  byId('analyzeButton').disabled = false;
  byId('cancelButton').disabled = false;
  byId('thresholdValue').textContent = '75%';
  setInputMode(state.inputMode);
  updateFileCard(1);
  updateFileCard(2);
  clearError();
  if (safeNotice) showError(safeNotice);
  byId('analyzer').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function downloadReport(event) {
  event.preventDefault();
  if (!state.jobId || !state.jobToken || byId('downloadReport').classList.contains('disabled')) return;
  const link = byId('downloadReport');
  const originalText = link.textContent;
  link.textContent = 'Preparing PDF…';
  link.classList.add('disabled');
  try {
    const response = await fetch(`/api/report/${encodeURIComponent(state.jobId)}.pdf`, {
      headers: { 'X-Job-Token': state.jobToken },
      cache: 'no-store',
    });
    if (!response.ok) {
      const payload = await readJson(response);
      throw new Error(payload.error || 'The report could not be downloaded.');
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const downloader = document.createElement('a');
    downloader.href = objectUrl;
    downloader.download = `verireel-report-${state.jobId.slice(0, 8)}.pdf`;
    document.body.appendChild(downloader);
    downloader.click();
    downloader.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) {
    showError(error.message || 'The report could not be downloaded.');
  } finally {
    link.textContent = originalText;
    link.classList.remove('disabled');
  }
}

function showError(message) {
  byId('formErrorText').textContent = message;
  byId('formError').classList.remove('hidden');
}

function clearError() {
  byId('formError').classList.add('hidden');
  byId('formErrorText').textContent = '';
}

async function readJson(response) {
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    return { error: `Unexpected server response (${response.status}).` };
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatCount(value) {
  const count = Math.max(0, Number(value) || 0);
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(count >= 10_000_000 ? 0 : 1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(count >= 100_000 ? 0 : 1)}K`;
  return String(Math.round(count));
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const remainder = Math.round(value % 60);
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function shortDigest(value) {
  const digest = String(value || '');
  return digest.length >= 16 ? `${digest.slice(0, 16)}…` : 'Not available';
}

function clampScore(value) {
  return Math.max(0, Math.min(100, Number(value) || 0));
}

function formatScore(value) {
  return `${clampScore(value).toFixed(1)}%`;
}

function average(...values) {
  const numbers = values.map((value) => Number(value) || 0);
  return numbers.reduce((sum, value) => sum + value, 0) / Math.max(1, numbers.length);
}
