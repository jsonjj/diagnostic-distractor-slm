import { assertValidEncounter } from "./content.js";

const encounter = {
  id: "prototype-fraction-foundry-001",
  contentStatus: "prototype-placeholder",
  sourceSplit: "prototype",
  questionHash: "prototype:beacon-three-fourths-plus-one-eighth",
  district: "Fraction Foundry",
  roomLabel: "Checkpoint 1 of 3",
  question: {
    prompt:
      "A rally battery is 3/4 charged. The pit crew adds another 1/8 of a full charge. How full is it now?",
    topic: "Adding and Subtracting Fractions",
    correctAnswer: "7/8",
    roadEquation: "3/4 + 1/8 = ?",
    trustedSteps: ["3/4 = 6/8", "6/8 + 1/8 = 7/8"],
  },
  correctAnswerId: "answer-correct",
  featuredCounterfeitId: "counterfeit-denominator-devourer",
  repairChoices: [
    {
      id: "repair-equal-pieces",
      label: "Make equal-sized pieces first.",
      detail: "Rename 3/4 as 6/8, then add eighths.",
    },
    {
      id: "repair-keep-addition",
      label: "Keep addition, then multiply.",
      detail: "Multiplication would change the action in the story.",
    },
    {
      id: "repair-read-action",
      label: "Follow the action in the story.",
      detail: "The pit crew adds charge; it does not remove charge.",
    },
  ],
  counterfeits: [
    {
      id: "counterfeit-denominator-devourer",
      answerId: "answer-add-denominators",
      answer: "4/12",
      misconception: "Adds numerators and denominators directly",
      computation: "3/4 + 1/8 = (3 + 1)/(4 + 8) = 4/12",
      glitchFamilyId: "denominator-devourer",
      glitchName: "Denominator Devourer",
      repairId: "repair-equal-pieces",
      repairExplanation:
        "Fractions must describe equal-sized pieces before their numerators can be combined.",
    },
    {
      id: "counterfeit-operation-mimic",
      answerId: "answer-multiply",
      answer: "3/32",
      misconception: "Multiplies instead of adding",
      computation: "3/4 x 1/8 = 3/32",
      glitchFamilyId: "operation-mimic",
      glitchName: "Operation Mimic",
      repairId: "repair-keep-addition",
      repairExplanation:
        "The crew adds charge, so the mathematical operation must remain addition.",
    },
    {
      id: "counterfeit-sign-switcher",
      answerId: "answer-subtract",
      answer: "5/8",
      misconception: "Subtracts instead of adding",
      computation: "3/4 - 1/8 = 6/8 - 1/8 = 5/8",
      glitchFamilyId: "sign-switcher",
      glitchName: "Sign Switcher",
      repairId: "repair-read-action",
      repairExplanation:
        "The word adds tells us the battery gains charge rather than losing it.",
    },
  ],
  provenance: {
    sourceType: "prototype-placeholder",
    note:
      "Hand-authored interaction fixture for the graybox; this is not an SLM generation and is not approved gameplay content.",
  },
};

export const prototypeEncounter = assertValidEncounter(encounter);

const decimalDocksEncounter = assertValidEncounter({
  id: "prototype-decimal-docks-002",
  contentStatus: "prototype-placeholder",
  sourceSplit: "prototype",
  questionHash: "prototype:coolant-two-point-four-plus-zero-point-six-five",
  district: "Decimal Docks",
  roomLabel: "Checkpoint 2 of 3",
  question: {
    prompt:
      "A repair drone carries 2.4 liters of coolant, then loads 0.65 liter more. How many liters does it carry now?",
    topic: "Adding and Subtracting with Decimals",
    correctAnswer: "3.05",
    roadEquation: "2.4 + 0.65 = ?",
    trustedSteps: ["2.4 = 2.40", "2.40 + 0.65 = 3.05"],
  },
  correctAnswerId: "decimal-answer-correct",
  featuredCounterfeitId: "counterfeit-decimal-shifter",
  repairChoices: [
    {
      id: "repair-align-decimals",
      label: "Line up equal place values.",
      detail: "Write 2.4 as 2.40, then add tenths to tenths and hundredths to hundredths.",
    },
    {
      id: "repair-follow-coolant",
      label: "Keep the story's addition.",
      detail: "Loading more coolant increases the amount instead of subtracting it.",
    },
    {
      id: "repair-protect-hundredths",
      label: "Keep 0.65 as sixty-five hundredths.",
      detail: "Moving its decimal point would change the amount by a factor of ten.",
    },
  ],
  counterfeits: [
    {
      id: "counterfeit-place-value-jammer",
      answerId: "decimal-answer-append",
      answer: "2.105",
      misconception: "Appends decimal digits instead of adding equal place values",
      computation: "2.4 + 0.65 = 2.105",
      glitchFamilyId: "wild-fault",
      glitchName: "Place-Value Jammer",
      repairId: "repair-align-decimals",
      repairExplanation:
        "Decimal points align ones, tenths, and hundredths; adding digit strings does not preserve those values.",
    },
    {
      id: "counterfeit-coolant-reverser",
      answerId: "decimal-answer-subtract",
      answer: "1.75",
      misconception: "Subtracts the new coolant instead of adding it",
      computation: "2.40 - 0.65 = 1.75",
      glitchFamilyId: "sign-switcher",
      glitchName: "Coolant Reverser",
      repairId: "repair-follow-coolant",
      repairExplanation:
        "The drone loads more coolant, so the amount grows through addition.",
    },
    {
      id: "counterfeit-decimal-shifter",
      answerId: "decimal-answer-shift",
      answer: "8.9",
      misconception: "Reads 0.65 as 6.5 by shifting the decimal point",
      computation: "2.4 + 6.5 = 8.9",
      glitchFamilyId: "operation-mimic",
      glitchName: "Decimal Shifter",
      repairId: "repair-protect-hundredths",
      repairExplanation:
        "The number 0.65 means sixty-five hundredths; shifting its decimal makes a different quantity.",
    },
  ],
  provenance: {
    sourceType: "prototype-placeholder",
    note:
      "Hand-authored interaction fixture for the graybox; this is not an SLM generation and is not approved gameplay content.",
  },
});

const integerIcewayEncounter = assertValidEncounter({
  id: "prototype-integer-iceway-003",
  contentStatus: "prototype-placeholder",
  sourceSplit: "prototype",
  questionHash: "prototype:tunnel-lift-negative-two-descends-seven",
  district: "Integer Iceway",
  roomLabel: "Checkpoint 3 of 3",
  question: {
    prompt:
      "A tunnel lift begins at level -2, then descends 7 levels. At what level does it stop?",
    topic: "Adding and Subtracting Negative Numbers",
    correctAnswer: "-9",
    roadEquation: "-2 - 7 = ?",
    trustedSteps: [
      "Descending 7 levels means subtract 7.",
      "-2 - 7 = -9",
    ],
  },
  correctAnswerId: "integer-answer-correct",
  featuredCounterfeitId: "counterfeit-sign-scrubber",
  repairChoices: [
    {
      id: "repair-move-left",
      label: "Move left when the lift descends.",
      detail: "Start at -2 and count seven more levels below zero.",
    },
    {
      id: "repair-keep-sign",
      label: "Keep the below-zero sign.",
      detail: "A level below zero needs a negative sign.",
    },
    {
      id: "repair-keep-order",
      label: "Keep the starting level first.",
      detail: "Model the story as -2 - 7, not 7 - 2 or -7 - (-2).",
    },
  ],
  counterfeits: [
    {
      id: "counterfeit-direction-flipper",
      answerId: "integer-answer-positive-five",
      answer: "5",
      misconception: "Reverses the subtraction and moves toward positive levels",
      computation: "7 - 2 = 5",
      glitchFamilyId: "sign-switcher",
      glitchName: "Direction Flipper",
      repairId: "repair-move-left",
      repairExplanation:
        "Descending from -2 travels left on the number line, reaching -9 rather than a positive level.",
    },
    {
      id: "counterfeit-sign-scrubber",
      answerId: "integer-answer-positive-nine",
      answer: "9",
      misconception: "Adds the distances but drops the negative sign",
      computation: "2 + 7 = 9",
      glitchFamilyId: "wild-fault",
      glitchName: "Sign Scrubber",
      repairId: "repair-keep-sign",
      repairExplanation:
        "The lift remains below level zero, so the total distance must be written as -9.",
    },
    {
      id: "counterfeit-order-swapper",
      answerId: "integer-answer-negative-five",
      answer: "-5",
      misconception: "Swaps the starting level and the downward change",
      computation: "-7 - (-2) = -5",
      glitchFamilyId: "operation-mimic",
      glitchName: "Order Swapper",
      repairId: "repair-keep-order",
      repairExplanation:
        "The lift starts at -2 and then subtracts 7; keeping that order gives -9.",
    },
  ],
  provenance: {
    sourceType: "prototype-placeholder",
    note:
      "Hand-authored interaction fixture for the graybox; this is not an SLM generation and is not approved gameplay content.",
  },
});

export const prototypeEncounters = [
  prototypeEncounter,
  decimalDocksEncounter,
  integerIcewayEncounter,
];
