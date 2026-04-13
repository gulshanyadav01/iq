import {process} from "std-env";

type ColorShades = {
  '50': string;
  '100': string;
  '200': string;
  '300': string;
  '400': string;
  '500': string;
  '600': string;
  '700': string;
  '800': string;
  '900': string;
  '950': string;
};

type ColorPalettes = {
  [key: string]: ColorShades;
};

interface UseThemeReturn {
  setPrimaryColor: (shades: Partial<ColorShades>) => void;
  setColorPalette: (colorName: string) => void;
}

export default function useTheme(): UseThemeReturn {
  const setPrimaryColor = (shades: Partial<ColorShades>): void => {
    for (const [key, value] of Object.entries(shades)) {
      if (value) {
        document.documentElement.style.setProperty(`--color-primary-${key}`, value);
      }
    }
  };

  const setColorPalette = (colorName: string): void => {
    const palettes: ColorPalettes = {
      indigo: {
        '50': '238 242 255',
        '100': '224 231 255',
        '200': '199 210 254',
        '300': '165 181 252',
        '400': '128 141 249',
        '500': '98 102 242',
        '600': '79 71 230',
        '700': '65 55 203',
        '800': '54 47 164',
        '900': '48 46 129',
        '950': '30 27 75',
      },
      emerald: {
        '50': '236 253 245',
        '100': '209 250 229',
        '200': '167 243 208',
        '300': '110 231 183',
        '400': '52 211 153',
        '500': '16 185 129',
        '600': '5 150 105',
        '700': '4 120 87',
        '800': '6 95 70',
        '900': '6 78 59',
        '950': '2 34 22'
      },
      marigold: {
        '50': '250 248 236',
        '100': '244 238 205',
        '200': '233 221 159',
        '300': '221 197 103',
        '400': '209 174 62',
        '500': '182 143 45',
        '600': '167 121 39',
        '700': '134 89 34',
        '800': '112 73 35',
        '900': '96 61 35',
        '950': '55 31 17'
      },
      rose: {
        '50': '255 241 242',
        '100': '255 228 230',
        '200': '254 205 211',
        '300': '253 164 175',
        '400': '251 113 133',
        '500': '244 63 94',
        '600': '225 29 72',
        '700': '190 18 60',
        '800': '159 18 57',
        '900': '136 19 55',
        '950': '80 7 36'
      },
      purple: {
        '50': '250 245 255',
        '100': '243 232 255',
        '200': '233 213 255',
        '300': '216 180 254',
        '400': '192 132 252',
        '500': '168 85 247',
        '600': '147 51 234',
        '700': '126 34 206',
        '800': '107 33 168',
        '900': '88 28 135',
        '950': '59 7 100'
      },
      cyan: {
        '50': '236 254 255',
        '100': '207 250 254',
        '200': '165 243 252',
        '300': '103 232 249',
        '400': '34 211 238',
        '500': '6 182 212',
        '600': '8 145 178',
        '700': '14 116 144',
        '800': '21 94 117',
        '900': '22 78 99',
        '950': '8 43 59'
      },
      amber: {
        '50': '255 251 235',
        '100': '254 243 199',
        '200': '253 230 138',
        '300': '252 211 77',
        '400': '251 191 36',
        '500': '217 119 6',
        '600': '183 85 8',
        '700': '147 64 9',
        '800': '120 53 15',
        '900': '93 41 15',
        '950': '49 22 7'
      },
      blue: {
        '50': '239 246 255',
        '100': '219 234 254',
        '200': '191 219 254',
        '300': '147 197 253',
        '400': '96 165 250',
        '500': '59 130 246',
        '600': '37 99 235',
        '700': '29 78 216',
        '800': '30 64 175',
        '900': '30 58 138',
        '950': '23 37 84'
      }
    };

    const palette = palettes[colorName] || palettes.indigo;
    setPrimaryColor(palette);

    // Optional: persist to localStorage
    if (process.client) {
      localStorage.setItem('colorPalette', colorName);
    }
  };

  // Initialize with saved palette
  if (process.client) {
    const savedPalette = localStorage.getItem('colorPalette');
    if (savedPalette) {
      setColorPalette(savedPalette);
    }
  }

  return {setPrimaryColor, setColorPalette};
}