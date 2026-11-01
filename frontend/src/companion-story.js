const movie = durationFor => Array.from({ length: 48 }, (_, index) => [index, durationFor(index)]);
/** Accelerating projectile, overlapping entrances and no artificial inter-beat holds. */
export const storyFrames = Object.freeze({
  "cow-to-horse": movie(i => i === 0 ? 120 : i < 3 ? 80 : i < 8 ? 45 : i < 12 ? 70 :
    i < 18 ? 100 : i < 38 ? 130 : i < 46 ? 120 : i === 46 ? 100 : 130),
  "horse-to-cow": movie(i => i === 0 ? 110 : i < 4 ? 70 : i < 7 ? 90 : i < 18 ? 100 :
    i < 27 ? 110 : i < 29 ? 100 : i < 36 ? 120 : i < 43 ? 130 : i < 47 ? 110 : 130),
});
