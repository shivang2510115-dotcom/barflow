/**
 * Resolve a palette token, keeping Tailwind's opacity modifier working.
 *
 * The tokens in index.css hold space-separated RGB channels rather than hex, which is
 * what makes `bg-brass/10` possible: Tailwind substitutes the alpha into the slot, and
 * a bare `var(--brass)` holding `#a85b18` would have nowhere to put it and would drop
 * the opacity silently. The rooms grid tints every occupied tile that way, so this is
 * load-bearing rather than tidiness.
 */
const withOpacity = (variable) => ({ opacityValue }) =>
  opacityValue === undefined
    ? `rgb(var(${variable}))`
    : `rgb(var(${variable}) / ${opacityValue})`;

/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
    extend: {
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)'
      },
      colors: {
        // The app's own palette, as roles rather than shades — see index.css for what
        // each one means and why. Each token is stored there as RGB channels with its
        // hex kept alongside in a comment, so contrast stays checkable by eye without
        // decoding three integers.
        //
        // Written through `withOpacity` so that `bg-brass/10` still works: a bare
        // `var(--brass)` would silently drop the opacity modifier, and the rooms grid
        // depends on tinted fills.
        ground: withOpacity('--ground'),
        surface: withOpacity('--surface'),
        raised: withOpacity('--raised'),

        ink: withOpacity('--ink'),
        muted2: withOpacity('--muted'),
        faint: withOpacity('--faint'),

        hairline: withOpacity('--hairline'),
        'hairline-strong': withOpacity('--hairline-strong'),

        brass: withOpacity('--brass'),
        'brass-deep': withOpacity('--brass-deep'),
        'on-brass': withOpacity('--on-brass'),

        // Room state. Never the brand hue — see the note in index.css.
        'state-free': withOpacity('--state-free'),
        'state-occupied': withOpacity('--state-occupied'),
        'state-dirty': withOpacity('--state-dirty'),
        'state-alert': withOpacity('--state-alert'),
        'state-inspected': withOpacity('--state-inspected'),

        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))'
        }
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0'
          },
          to: {
            height: 'var(--radix-accordion-content-height)'
          }
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)'
          },
          to: {
            height: '0'
          }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out'
      }
    }
  },
  plugins: [require("tailwindcss-animate")],
};