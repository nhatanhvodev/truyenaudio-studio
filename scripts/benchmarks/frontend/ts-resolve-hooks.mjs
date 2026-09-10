/**
 * V02 — resolve hook cho driver Node của harness benchmark.
 *
 * Source frontend dùng import kiểu bundler ('./foo' không có đuôi). Node ESM thuần
 * không tự thêm '.ts', nên hook này thử './foo.ts' trước khi bỏ cuộc. Nhờ vậy driver
 * import được CHÍNH module thật của frontend (không sao chép logic sang JS).
 */

const TS_EXTENSION = /\.[cm]?[jt]sx?$/;

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('.') && !TS_EXTENSION.test(specifier)) {
    try {
      return await nextResolve(specifier + '.ts', context);
    } catch {
      // Rơi xuống resolver mặc định để lỗi thật vẫn nổi lên nguyên vẹn.
    }
  }
  return nextResolve(specifier, context);
}
