class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.offset = 0;
    this.pending = [];
    this.port.onmessage = () => this.flush();
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    let offset = this.offset;
    while (offset < channel.length) {
      const index = Math.floor(offset);
      const next = Math.min(index + 1, channel.length - 1);
      const fraction = offset - index;
      const sample = channel[index] * (1 - fraction) + channel[next] * fraction;
      this.pending.push(sample);
      offset += this.ratio;
    }
    this.offset = offset - channel.length;
    if (this.pending.length >= 1600) this.flush();
    return true;
  }

  flush() {
    if (!this.pending.length) return;
    const pcm = new Int16Array(this.pending.length);
    for (let i = 0; i < this.pending.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, this.pending[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    this.pending = [];
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
