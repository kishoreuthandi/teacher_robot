const mouth = document.getElementById('mouth');
const eyes = [...document.querySelectorAll('.eye')];
const lidsTop = [...document.querySelectorAll('.lid-top')];
const lidsBottom = [...document.querySelectorAll('.lid-bottom')];

const shapes = ['rest', 'a', 'i', 'o', 'u', 'i', 'rest'];
let shapeIndex = 0;
let speaking = false;
let lastFaceStateAt = 0;

function setMouth(shape) {
  mouth.className = `mouth ${shape}`;
}

function blink() {
  lidsTop.forEach(lid => lid.style.height = '58%');
  lidsBottom.forEach(lid => lid.style.height = '48%');
  setTimeout(() => {
    lidsTop.forEach(lid => lid.style.height = '0%');
    lidsBottom.forEach(lid => lid.style.height = '0%');
  }, 90);
  setTimeout(blink, 2200 + Math.random() * 4200);
}

function idleEyes() {
  const x = Math.sin(Date.now() / 1300) * 0.8;
  const y = Math.cos(Date.now() / 1700) * 0.45;
  eyes.forEach((eye, index) => {
    const side = index === 0 ? -1 : 1;
    eye.style.transform = `translate(${x * side}vmin, ${y}vmin)`;
  });
  requestAnimationFrame(idleEyes);
}

function mouthLoop() {
  if (speaking) {
    setMouth(shapes[shapeIndex % shapes.length]);
    shapeIndex += 1;
  } else {
    setMouth('rest');
  }
  setTimeout(mouthLoop, speaking ? 115 : 450);
}

async function pollSpeakingState() {
  try {
    const response = await fetch('http://127.0.0.1:8000/face/state', { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`face state ${response.status}`);
    }
    const state = await response.json();
    speaking = Boolean(state.speaking);
    lastFaceStateAt = Date.now();
  } catch (error) {
    if (Date.now() - lastFaceStateAt > 1500) {
      speaking = false;
    }
  } finally {
    setTimeout(pollSpeakingState, speaking ? 80 : 180);
  }
}

setMouth('rest');
blink();
idleEyes();
mouthLoop();
pollSpeakingState();
