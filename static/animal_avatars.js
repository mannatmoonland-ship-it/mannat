const avatarDefinitions = [
  { id: "cute_cat", name: "Curious Cat", group: "Cats", color: "#f6d5c2", fur: "#d88a63", detail: "#fff0df", ears: "point", feature: "cat" },
  { id: "puppy", name: "Happy Puppy", group: "Dogs", color: "#d9e8ff", fur: "#b8794d", detail: "#f1cba8", ears: "floppy", feature: "dog" },
  { id: "bear", name: "Little Bear", group: "Bears", color: "#f3dfc9", fur: "#9b6549", detail: "#e7b78e", ears: "round", feature: "bear" },
  { id: "panda", name: "Panda Pal", group: "Bears", color: "#e4e9ef", fur: "#f7f7f2", detail: "#252d36", ears: "round", feature: "panda" },
  { id: "rabbit", name: "Bunny", group: "Forest friends", color: "#f5ddeb", fur: "#fff4f6", detail: "#ed9ab1", ears: "tall", feature: "rabbit" },
  { id: "fox", name: "Clever Fox", group: "Forest friends", color: "#ffdfc6", fur: "#df7442", detail: "#fff2df", ears: "point", feature: "fox" },
  { id: "lion", name: "Brave Lion", group: "Wild friends", color: "#ffe4aa", fur: "#d88937", detail: "#f4bd61", ears: "round", feature: "lion" },
  { id: "tiger", name: "Tiny Tiger", group: "Wild friends", color: "#ffdbb5", fur: "#ee8b35", detail: "#332927", ears: "round", feature: "tiger" },
  { id: "elephant", name: "Gentle Elephant", group: "Wild friends", color: "#d8e8f2", fur: "#829fb3", detail: "#adc7d6", ears: "wide", feature: "elephant" },
  { id: "monkey", name: "Cheeky Monkey", group: "Wild friends", color: "#f6dfc8", fur: "#956345", detail: "#f4d5ae", ears: "round", feature: "monkey" },
  { id: "owl", name: "Wise Owl", group: "Birds", color: "#e3dcf5", fur: "#967bb4", detail: "#f4e7c4", ears: "tuft", feature: "owl" },
  { id: "penguin", name: "Waddling Penguin", group: "Birds", color: "#d9edf5", fur: "#384d62", detail: "#f8faf8", ears: "none", feature: "penguin" },
  { id: "parrot", name: "Bright Parrot", group: "Birds", color: "#d8f1d8", fur: "#39a879", detail: "#f2bf4c", ears: "crest", feature: "parrot" },
  { id: "chicken", name: "Little Chicken", group: "Farm friends", color: "#fff0bf", fur: "#fff8dc", detail: "#ec7656", ears: "comb", feature: "chicken" },
  { id: "cow", name: "Daisy Cow", group: "Farm friends", color: "#e9e6e1", fur: "#f8f6f0", detail: "#35373b", ears: "horns", feature: "cow" },
  { id: "pig", name: "Pink Piglet", group: "Farm friends", color: "#f9dce2", fur: "#f2a9b5", detail: "#dc7888", ears: "pig", feature: "pig" },
  { id: "sheep", name: "Fluffy Sheep", group: "Farm friends", color: "#e7e8d8", fur: "#fffaf0", detail: "#d6a898", ears: "wool", feature: "sheep" },
  { id: "horse", name: "Sunny Horse", group: "Farm friends", color: "#f4e1c5", fur: "#a96c48", detail: "#4d3631", ears: "point", feature: "horse" },
  { id: "goat", name: "Goofy Goat", group: "Farm friends", color: "#e4eee0", fur: "#eee8d9", detail: "#987a62", ears: "horns", feature: "goat" },
  { id: "duck", name: "Ducky", group: "Farm friends", color: "#fff0b5", fur: "#f4d95e", detail: "#ed9c40", ears: "none", feature: "duck" },
  { id: "whale", name: "Happy Whale", group: "Sea friends", color: "#d5eff8", fur: "#4e9ec0", detail: "#a7d8e8", ears: "none", feature: "whale" },
  { id: "dolphin", name: "Playful Dolphin", group: "Sea friends", color: "#d9edfa", fur: "#669fc5", detail: "#b8d9eb", ears: "none", feature: "dolphin" },
  { id: "octopus", name: "Octopus Buddy", group: "Sea friends", color: "#f4d9ef", fur: "#bd78b5", detail: "#e5a9d8", ears: "none", feature: "octopus" },
  { id: "turtle", name: "Slow-and-Steady Turtle", group: "Sea friends", color: "#d9edcf", fur: "#68a77b", detail: "#b8d98d", ears: "none", feature: "turtle" },
  { id: "shark", name: "Smiley Shark", group: "Sea friends", color: "#dce7ed", fur: "#7793a5", detail: "#eff5f5", ears: "none", feature: "shark" },
  { id: "dino", name: "Friendly Dino", group: "Dinosaurs", color: "#d9efd8", fur: "#69a978", detail: "#a5d58f", ears: "spikes", feature: "dino" },
  { id: "trex", name: "Tiny T-Rex", group: "Dinosaurs", color: "#e3efd0", fur: "#86a64e", detail: "#c3d77c", ears: "spikes", feature: "trex" },
  { id: "unicorn", name: "Dreamy Unicorn", group: "Fantasy friends", color: "#f0ddfb", fur: "#fff7ff", detail: "#d78ad1", ears: "horse", feature: "unicorn" },
  { id: "dragon", name: "Kind Dragon", group: "Fantasy friends", color: "#d7eee9", fur: "#4d9b89", detail: "#9bd3a5", ears: "horns", feature: "dragon" },
  { id: "sloth", name: "Sleepy Sloth", group: "Funny friends", color: "#e4dfd2", fur: "#927b61", detail: "#f2e7d3", ears: "round", feature: "sloth" },
  { id: "raccoon", name: "Raccoon Scout", group: "Funny friends", color: "#dfe5ec", fur: "#778391", detail: "#303944", ears: "round", feature: "raccoon" },
  { id: "koala", name: "Cuddly Koala", group: "Funny friends", color: "#dce8ef", fur: "#9aaab5", detail: "#f1f4f4", ears: "wide", feature: "koala" },
  { id: "hedgehog", name: "Prickly-Cute Hedgehog", group: "Funny friends", color: "#f0e0ce", fur: "#9c765b", detail: "#d8b294", ears: "spikes", feature: "hedgehog" },
  { id: "otter", name: "River Otter", group: "Funny friends", color: "#d9edf0", fur: "#987456", detail: "#e8caa3", ears: "round", feature: "otter" },
  { id: "frog", name: "Jolly Frog", group: "Funny friends", color: "#d9f0d4", fur: "#70b967", detail: "#e3f4b4", ears: "eyes", feature: "frog" },
  { id: "seal", name: "Sea-Side Seal", group: "Sea friends", color: "#e2e8f0", fur: "#8b9baa", detail: "#eef1f1", ears: "none", feature: "seal" },
];

function avatarSvgMarkup(avatar) {
  const { color, fur, detail, feature, ears } = avatar;
  const parts = [`<circle cx="50" cy="50" r="49" fill="${color}"/>`];

  const earMarkup = {
    point: '<path d="M28 42 29 17l18 18M72 42 71 17 53 35" fill="FUR" stroke="#493b38" stroke-width="2.5" stroke-linejoin="round"/><path d="m33 34-1-10 9 11m26 0 9-11-1 10" fill="DETAIL"/>',
    floppy: '<ellipse cx="25" cy="51" rx="10" ry="19" transform="rotate(-22 25 51)" fill="FUR" stroke="#493b38" stroke-width="2.5"/><ellipse cx="75" cy="51" rx="10" ry="19" transform="rotate(22 75 51)" fill="FUR" stroke="#493b38" stroke-width="2.5"/>',
    round: '<circle cx="32" cy="35" r="11" fill="FUR" stroke="#493b38" stroke-width="2.5"/><circle cx="68" cy="35" r="11" fill="FUR" stroke="#493b38" stroke-width="2.5"/>',
    tall: '<path d="M31 42Q22 7 38 12Q49 17 46 43M69 42Q78 7 62 12Q51 17 54 43" fill="FUR" stroke="#493b38" stroke-width="2.5"/><path d="M34 34Q29 17 37 19Q42 22 42 37m24-3q5-17-3-15-5 3-5 18" fill="DETAIL"/>',
    wide: '<ellipse cx="25" cy="50" rx="16" ry="20" fill="DETAIL" stroke="#493b38" stroke-width="2.5"/><ellipse cx="75" cy="50" rx="16" ry="20" fill="DETAIL" stroke="#493b38" stroke-width="2.5"/>',
    tuft: '<path d="m31 39-8-16 18 10m28 6 8-16-18 10" fill="FUR" stroke="#493b38" stroke-width="2.5" stroke-linejoin="round"/>',
    crest: '<path d="M42 30Q30 15 40 13Q51 16 50 29Q52 10 61 14Q67 19 56 31" fill="DETAIL" stroke="#493b38" stroke-width="2"/>',
    comb: '<path d="M42 31Q34 21 42 17Q45 14 48 22Q52 10 57 16Q62 18 57 29" fill="DETAIL" stroke="#493b38" stroke-width="2"/>',
    horns: '<path d="M35 38Q22 25 31 17Q37 22 41 34m18 0q4-12 10-17 9 8-4 21" fill="#f1d8a8" stroke="#493b38" stroke-width="2.5"/>',
    pig: '<path d="M28 43Q19 19 39 29L45 40m27 3q9-24-11-14l-6 12" fill="FUR" stroke="#493b38" stroke-width="2.5"/>',
    wool: '<circle cx="32" cy="38" r="12" fill="DETAIL"/><circle cx="68" cy="38" r="12" fill="DETAIL"/>',
    horse: '<path d="M34 39 32 16l16 19m18 4 2-23-16 19" fill="FUR" stroke="#493b38" stroke-width="2.5" stroke-linejoin="round"/>',
    spikes: '<path d="m31 38 1-13 10 8 8-14 8 14 10-8 1 15" fill="DETAIL" stroke="#493b38" stroke-width="2.5" stroke-linejoin="round"/>',
    eyes: '<circle cx="36" cy="34" r="12" fill="DETAIL" stroke="#493b38" stroke-width="2.5"/><circle cx="64" cy="34" r="12" fill="DETAIL" stroke="#493b38" stroke-width="2.5"/>',
    none: "",
  }[ears].replaceAll("FUR", fur).replaceAll("DETAIL", detail);

  parts.push(earMarkup);
  if (feature === "lion") parts.push('<circle cx="50" cy="53" r="36" fill="#d88937" stroke="#493b38" stroke-width="2.5"/>');
  if (feature === "turtle") parts.push('<ellipse cx="50" cy="62" rx="30" ry="20" fill="#83b96e" stroke="#493b38" stroke-width="2.5"/><path d="M50 42v39M22 62h56" stroke="#548a5c" stroke-width="2"/>');
  if (feature === "hedgehog") parts.push('<path d="m22 55 7-20 8 13 7-22 8 22 9-21 6 23 12-13-1 25-9 12H32z" fill="#80624f" stroke="#493b38" stroke-width="2.5" stroke-linejoin="round"/>');

  const head = ["whale", "dolphin", "shark", "seal"].includes(feature)
    ? '<ellipse cx="50" cy="55" rx="33" ry="27"'
    : '<ellipse cx="50" cy="54" rx="27" ry="26"';
  parts.push(`${head} fill="${fur}" stroke="#493b38" stroke-width="2.5"/>`);
  if (feature === "panda") parts.push('<ellipse cx="38" cy="51" rx="8" ry="11" fill="#252d36" transform="rotate(25 38 51)"/><ellipse cx="62" cy="51" rx="8" ry="11" fill="#252d36" transform="rotate(-25 62 51)"/>');
  if (feature === "raccoon") parts.push('<path d="M26 48Q50 38 74 48L69 60 58 58 50 65 42 58 31 60z" fill="#303944"/>');
  if (feature === "sloth") parts.push('<ellipse cx="39" cy="51" rx="9" ry="13" fill="#ead8bd"/><ellipse cx="61" cy="51" rx="9" ry="13" fill="#ead8bd"/>');
  if (feature === "tiger") parts.push('<path d="m27 42 10 7-8 7m34-7 10-7-2 14M39 31l4 8m14-8-4 8" fill="none" stroke="#332927" stroke-width="4" stroke-linecap="round"/>');
  if (feature === "elephant") parts.push('<path d="M50 62v16q0 12 10 8 5-2 1-8" fill="none" stroke="#829fb3" stroke-width="9" stroke-linecap="round"/>');
  if (feature === "owl" || feature === "penguin" || feature === "chicken" || feature === "duck" || feature === "parrot") {
    parts.push('<ellipse cx="50" cy="61" rx="18" ry="14" fill="DETAIL"/>');
  }
  if (feature === "whale" || feature === "dolphin" || feature === "shark" || feature === "seal") {
    parts.push('<path d="M25 65Q50 84 75 65Q68 89 50 87Q32 86 25 65" fill="DETAIL" stroke="#493b38" stroke-width="1.5"/>');
  }
  if (feature === "octopus") parts.push('<path d="M29 72q-7 9 1 13 7 3 9-5m5 2q0 10 8 9 7-1 5-10m8 0q3 9 10 5 6-5-1-12" fill="none" stroke="#bd78b5" stroke-width="8" stroke-linecap="round"/>');
  if (feature === "unicorn") parts.push('<path d="m50 15 7 20-7 4-7-4z" fill="#f0c45b" stroke="#9c7944" stroke-width="2"/><path d="M48 20h4" stroke="#fff4b8" stroke-width="2"/>');
  if (feature === "dragon") parts.push('<path d="m30 38-10-9 2 17m48-8 10-9-2 17" fill="#81c5aa" stroke="#493b38" stroke-width="2.5" stroke-linejoin="round"/>');
  if (feature === "dino" || feature === "trex") parts.push('<circle cx="76" cy="67" r="3" fill="#eaf2bf"/><circle cx="81" cy="77" r="3" fill="#eaf2bf"/>');
  if (feature === "pig") parts.push('<ellipse cx="50" cy="64" rx="13" ry="9" fill="#dc7888"/><ellipse cx="46" cy="64" rx="2" ry="3" fill="#8f4e5a"/><ellipse cx="54" cy="64" rx="2" ry="3" fill="#8f4e5a"/>');
  if (feature === "cow") parts.push('<path d="M31 45q9 8 15-2m8 0q7 10 16 2" fill="#35373b"/>');
  if (feature === "goat") parts.push('<path d="m45 35 5 7 5-7" fill="#f1d8a8" stroke="#493b38" stroke-width="2"/>');
  if (feature === "horse") parts.push('<path d="M47 31q8-12 17-5l-8 10" fill="#4d3631"/>');
  if (feature === "sheep") parts.push('<circle cx="32" cy="38" r="8" fill="#fffaf0"/><circle cx="50" cy="31" r="9" fill="#fffaf0"/><circle cx="68" cy="38" r="8" fill="#fffaf0"/>');

  if (feature === "parrot") parts.push('<path d="m65 52 15 7-15 8q-8-4 0-15" fill="#ed9c40" stroke="#493b38" stroke-width="2"/>');
  else if (feature === "chicken" || feature === "duck") parts.push('<path d="m61 58 17 5-17 6" fill="#ed9c40" stroke="#493b38" stroke-width="2" stroke-linejoin="round"/>');
  else parts.push('<ellipse cx="50" cy="63" rx="13" ry="9" fill="DETAIL"/>');

  if (feature === "rabbit") parts.push('<ellipse cx="50" cy="63" rx="2.5" ry="2" fill="#9f5f70"/>');
  else if (feature !== "panda" && feature !== "tiger" && feature !== "raccoon" && feature !== "whale" && feature !== "dolphin" && feature !== "shark" && feature !== "seal" && feature !== "octopus") parts.push('<ellipse cx="50" cy="60" rx="3" ry="2.3" fill="#493b38"/>');

  if (feature !== "penguin" && feature !== "duck" && feature !== "chicken" && feature !== "parrot") {
    parts.push('<ellipse cx="40" cy="53" rx="3.2" ry="4.1" fill="#26343d"/><ellipse cx="60" cy="53" rx="3.2" ry="4.1" fill="#26343d"/><circle cx="41" cy="52" r="1" fill="#fff"/><circle cx="61" cy="52" r="1" fill="#fff"/>');
    if (feature !== "panda" && feature !== "raccoon") parts.push('<circle cx="33" cy="63" r="4" fill="#f2a3a1" opacity=".65"/><circle cx="67" cy="63" r="4" fill="#f2a3a1" opacity=".65"/>');
  } else {
    parts.push('<circle cx="41" cy="52" r="3.2" fill="#26343d"/><circle cx="59" cy="52" r="3.2" fill="#26343d"/><circle cx="42" cy="51" r="1" fill="#fff"/><circle cx="60" cy="51" r="1" fill="#fff"/>');
  }
  if (!["whale", "dolphin", "shark", "seal", "duck", "chicken", "parrot"].includes(feature)) {
    parts.push('<path d="M44 69q6 6 12 0" fill="none" stroke="#493b38" stroke-width="2" stroke-linecap="round"/>');
  }
  parts.push('<path d="M18 26h1m62 10h1M19 76h1m61 1h1" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".8"/>');

  return `<svg class="animal-avatar-svg" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${parts.join("")}</svg>`;
}

export const ANIMAL_AVATARS = Object.freeze(avatarDefinitions.map(avatar => Object.freeze(avatar)));

export function getAnimalAvatar(avatarId) {
  return ANIMAL_AVATARS.find(avatar => avatar.id === avatarId) || ANIMAL_AVATARS[0];
}

export function renderAnimalAvatar(avatarId) {
  return avatarSvgMarkup(getAnimalAvatar(avatarId));
}
