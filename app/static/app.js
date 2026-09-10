(function () {
  "use strict";

  document.querySelectorAll("[data-dismiss]").forEach(function (button) {
    button.addEventListener("click", function () {
      var notice = button.closest("[data-dismissible]");
      if (notice) {
        notice.hidden = true;
      }
    });
  });

  document.querySelectorAll("[data-money-input]").forEach(function (input) {
    var form = input.closest("form");
    var output = form ? form.querySelector("[data-money-output]") : null;
    if (!output) {
      return;
    }

    function updateMoneyPreview() {
      if (input.value === "") {
        output.textContent = "금액 미확인";
        return;
      }
      var amount = Number(input.value);
      if (!Number.isFinite(amount)) {
        output.textContent = "숫자로 입력해 주세요.";
        return;
      }
      output.textContent = new Intl.NumberFormat("ko-KR").format(amount) + "원";
    }

    input.addEventListener("input", updateMoneyPreview);
    updateMoneyPreview();
  });

  function openTargetDisclosure() {
    if (!window.location.hash) {
      return;
    }
    var target;
    try {
      target = document.querySelector(window.location.hash);
    } catch (error) {
      return;
    }
    if (target && target.tagName === "DETAILS") {
      target.open = true;
    }
  }

  window.addEventListener("hashchange", openTargetDisclosure);
  openTargetDisclosure();
})();
