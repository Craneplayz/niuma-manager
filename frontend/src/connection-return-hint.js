// Connection dialogs share the account panel's modal. Offer an optional return
// after dismissal without reopening it or replacing a successful save message.
export function createConnectionReturnHint({ getAccountVersion, isActiveAccount, isDialogOpen, notify, openAccount, schedule = queueMicrotask }) {
  let toastVersion = 0, lastToast = null, closeVersion = 0;
  return {
    recordToast(message, action) {
      toastVersion++;
      lastToast = { message, action };
    },
    closed(section) {
      if (!["pku-email", "teaching"].includes(section) || !isActiveAccount()) return;
      const epoch = getAccountVersion(), previousToast = toastVersion, operation = ++closeVersion;
      schedule(() => {
        if (operation !== closeVersion || epoch !== getAccountVersion() || !isActiveAccount() || isDialogOpen()) return;
        // A save/removal success toast is emitted just after closeDialog().
        if (toastVersion !== previousToast && lastToast?.action) return;
        const message = toastVersion !== previousToast ? lastToast.message : "连接设置已关闭";
        notify(message, () => {
          if (epoch === getAccountVersion() && isActiveAccount()) return openAccount(section === "pku-email" ? section : undefined);
        }, { actionLabel: "返回账号设置", durationMs: 10000 });
      });
    },
  };
}
