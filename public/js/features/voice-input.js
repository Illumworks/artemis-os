// Voice Input — Speech-to-Text using Web Speech API
import { $ } from '../core/dom.js';
import { getState } from '../core/store.js';
import { addStatus } from '../ui/messages.js';
import { getPane } from '../ui/parallel.js';

const hasBrowserApis =
  typeof window !== 'undefined' &&
  typeof document !== 'undefined' &&
  typeof navigator !== 'undefined';

function addNoSpeechClass() {
  document.body?.classList.add('no-speech-api');
}

const userAgent = hasBrowserApis ? navigator.userAgent || '' : '';
const isEdge = /Edg\//.test(userAgent);
const SpeechRecognition = hasBrowserApis
  ? (!isEdge && (window.SpeechRecognition || window.webkitSpeechRecognition))
  : null;

if (!SpeechRecognition) {
  addNoSpeechClass();
}

if (SpeechRecognition) {
  let recognition = null;
  let isRecording = false;
  let shouldRestart = false;
  let interimText = '';
  let preVoiceText = '';
  let finalizedText = '';

  function hideButton() {
    addNoSpeechClass();
    stopRecording();
  }

  // Recording indicator element (created once, reused)
  const indicator = document.createElement('div');
  indicator.className = 'voice-recording-indicator hidden';
  indicator.innerHTML = '<span class="voice-recording-dot"></span><span>Listening...</span>';

  // Support both legacy .input-bar shell and new .dp-composer shell
  const inputBar = $.messageInput?.closest('.input-bar');
  const dpComposer = $.messageInput?.closest('.dp-composer');
  const indicatorParent = inputBar?.parentNode || dpComposer?.parentNode;
  const indicatorAnchor = inputBar || dpComposer;

  const hasVoiceShell = !!(
    $.micBtn &&
    $.messageInput &&
    $.sendBtn &&
    $.newSessionBtn &&
    $.sessionList &&
    $.toggleParallelBtn &&
    indicatorParent
  );

  if (!hasVoiceShell) {
    addNoSpeechClass();
  } else {
    indicatorParent.insertBefore(indicator, indicatorAnchor);

    function updateTextarea() {
      $.messageInput.value = preVoiceText + finalizedText + interimText;
      // Trigger auto-resize
      $.messageInput.style.height = 'auto';
      $.messageInput.style.height = Math.min($.messageInput.scrollHeight, 200) + 'px';
    }

    function startRecording() {
      if (isRecording) return;
      if (getState('parallelMode')) {
        addStatus('Voice input is not available in parallel mode', true, getPane(null));
        return;
      }

      recognition = new SpeechRecognition();
      recognition.lang = 'en-US';
      recognition.continuous = true;
      recognition.interimResults = true;

    // Snapshot existing textarea content
    const current = $.messageInput.value;
    preVoiceText = current && !/\s$/.test(current) ? current + ' ' : current;
    finalizedText = '';
    interimText = '';

    recognition.onresult = (event) => {
      let finalChunk = '';
      let interimChunk = '';

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalChunk += transcript;
        } else {
          interimChunk += transcript;
        }
      }

      if (finalChunk) finalizedText += finalChunk;
      interimText = interimChunk;
      updateTextarea();
    };

    recognition.onend = () => {
      if (shouldRestart && isRecording) {
        // Chrome cuts off after ~60s of silence — restart
        try { recognition.start(); } catch (_) { stopRecording(); }
      } else {
        cleanUp();
      }
    };

    recognition.onerror = (event) => {
      const pane = getPane(null);
      switch (event.error) {
        case 'service-not-allowed':
        case 'language-not-supported':
          // API exists but browser doesn't actually support it (e.g. Edge)
          hideButton();
          break;
        case 'not-allowed':
          addStatus('Microphone access denied. Check browser permissions.', true, pane);
          stopRecording();
          break;
        case 'no-speech':
          // Silence — let onend restart if needed
          break;
        case 'network':
          addStatus('Speech recognition network error', true, pane);
          stopRecording();
          break;
        default:
          addStatus('Speech recognition error: ' + event.error, true, pane);
          stopRecording();
      }
    };

    isRecording = true;
    shouldRestart = true;
    $.micBtn.classList.add('recording');
    indicator.classList.remove('hidden');

    try {
      recognition.start();
    } catch (_) {
      // Constructor exists but service unavailable — hide permanently
      hideButton();
    }
  }

    function stopRecording() {
      shouldRestart = false;
      isRecording = false;
      if (recognition) {
        try { recognition.stop(); } catch (_) {}
      }
      cleanUp();
    }

    function cleanUp() {
      $.micBtn.classList.remove('recording');
      indicator.classList.add('hidden');
      // Commit any remaining interim text
      if (interimText) {
        finalizedText += interimText;
        interimText = '';
        updateTextarea();
      }
      recognition = null;
    }

    // Toggle on mic button click
    $.micBtn.addEventListener('click', () => {
      if (isRecording) {
        stopRecording();
      } else {
        startRecording();
      }
    });

    // Stop recording on send (capture phase so we commit text before sendMessage reads it)
    $.sendBtn.addEventListener('click', () => {
      if (isRecording) stopRecording();
    }, true);

    // Stop on Enter key (before chat.js handles it)
    $.messageInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && isRecording) {
        stopRecording();
      }
    }, true);

    // Stop on session switch
    $.newSessionBtn.addEventListener('click', () => {
      if (isRecording) stopRecording();
    });
    $.sessionList.addEventListener('click', () => {
      if (isRecording) stopRecording();
    });

    // Stop when parallel mode is toggled on
    $.toggleParallelBtn.addEventListener('change', () => {
      if (isRecording && $.toggleParallelBtn.checked) stopRecording();
    });

    // Stop when tab is hidden
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && isRecording) stopRecording();
    });
  }
}
