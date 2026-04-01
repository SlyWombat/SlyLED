Update with additional and updated built in actions:

1. "Solid Color" The most basic but essential action. It should support Hex codes (e.g., 0xFF0000) or RGB values (255, 0, 0) and a colour picker, it will also follow any global brightness settings

2. "Linear Fade" Create a function that calculates the "steps" between two colors over a set duration (e.g., 500ms). 

3. "Breathe"  A classic ambient effect where a single color fades in and out smoothly. The Math: Use a sin() or cos() wave to map the brightness. $$Brightness = \frac{(sin(time) + 1)}{2} \times 255$$

4. "Theater Chase" Every $n$-th pixel is lit, and the pattern shifts down the line. It’s the classic "movie theater" look. Use the modulo operator (%) 

5. "Rainbow" A function that cycles through the HSV (Hue, Saturation, Value) spectrum rather than RGB. It has a selection of 8 different colour palettes to choose from, from classic, to various complimentary colours. Speed option as well.

6. "Fire" This uses "Perlin Noise" or simple random variations. Instead of pure randomness (which looks like a strobe light), you want "smoothed" randomness where the brightness of a pixel only changes by a small amount relative to its previous state.

7. "Comet" One bright pixel (the head) "shoots" down the strip, leaving behind a trail of pixels that fade out over time. This requires a "fade-to-black" function that runs on every frame for the whole strip.

8. "Twinkle" Randomly pick $X$ number of pixels and turn them on to a random brightness, then fade them out. 

9. "Blackout" You need a clean function that sets all numLEDs to (0,0,0) and updates the strip. This was our previous "off"

A runner has a "Global Brightness" variable , defaulting to full brightness

Each of the actions that are sent to each child should have a unique identifier created and managed by the parent, the child should do a lightweight udp broadcast that would be an indication that the action has fired and when it ends. The parent, on the dashboard tab would be able to display the status of each child and the current action based on receiving these notifications. For each active runner there should be a progress bar that has the duration as the end point and a marker that moves along the bar (moving in real time), and below that moving marker each child that starts an action would be down below that current time mark.

Update all applicable documentation for these changes, increment to another major release, create comprehensive tests, look for other tests that might be missing. Rebuild, and test.