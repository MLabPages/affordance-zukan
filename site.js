(function () {
  "use strict";

  var chips = document.querySelectorAll(".chip[data-filter]");
  var cards = document.querySelectorAll(".card[data-category]");
  var empty = document.getElementById("filter-empty");
  if (!chips.length) return;

  function readHash() {
    var match = /^#field=(.*)$/.exec(location.hash);
    if (!match) return "all";
    try {
      return decodeURIComponent(match[1]);
    } catch (error) {
      return "all";
    }
  }

  function select(value) {
    var known = false;
    chips.forEach(function (chip) {
      if ((chip.getAttribute("data-filter") || "") === value) known = true;
    });
    if (!known) value = "all";

    chips.forEach(function (chip) {
      var on = (chip.getAttribute("data-filter") || "") === value;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });

    var shown = 0;
    cards.forEach(function (card) {
      var match = value === "all" || card.getAttribute("data-category") === value;
      card.hidden = !match;
      if (match) shown += 1;
    });
    if (empty) empty.hidden = shown !== 0;
    return value;
  }

  function writeHash(value) {
    var base = location.pathname + location.search;
    var next = value === "all" ? base : base + "#field=" + encodeURIComponent(value);
    if (location.pathname + location.search + location.hash !== next) {
      history.replaceState(null, "", next);
    }
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var value = select(chip.getAttribute("data-filter") || "all");
      writeHash(value);
    });
  });

  window.addEventListener("hashchange", function () {
    select(readHash());
  });

  select(readHash());
})();
