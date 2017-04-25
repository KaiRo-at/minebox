package io.minebox.nbd.ep;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;

import com.google.common.annotations.VisibleForTesting;
import com.google.inject.Inject;
import io.minebox.config.MinebdConfig;
import io.minebox.nbd.Encryption;
import io.minebox.nbd.MetadataService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class BucketFactory {
    private final String parentDir;
    private final long size;
    private final Encryption encryption;
    private final MetadataService metadataService;
    private final File parentFolder;

    @Inject
    public BucketFactory(MinebdConfig config, Encryption encryption, MetadataService metadataService) {
        this.parentDir = config.parentDir;
        this.size = config.bucketSize.toBytes();
        this.encryption = encryption;
        this.metadataService = metadataService;
        parentFolder = new File(parentDir, encryption.getPublicIdentifier());
        parentFolder.mkdirs();
    }

    public Bucket create(Integer bucketIndex) {
        return new BucketImpl(bucketIndex);
    }

    private static final Logger logger = LoggerFactory.getLogger(BucketImpl.class);

    class BucketImpl implements Bucket {

        private final FileChannel channel;
        private final long baseOffset;
        /**
         * highest valid offset, given minimum length of 1
         */
        private final long upperBound;
        private RandomAccessFile randomAccessFile;
        private final String filename;
        private final long bucketNumber;

        BucketImpl(long bucketNumber) {
            this.bucketNumber = bucketNumber;
            baseOffset = bucketNumber * size;
            upperBound = baseOffset + size - 1;
            filename = "minebox_v1_" + bucketNumber + ".dat";

            final File file = new File(parentFolder, filename);
            logger.debug("starting to monitor bucket {} with file {}", bucketNumber, file.getAbsolutePath());
            ensureFileExists(file);
            try {
                randomAccessFile = new RandomAccessFile(file, "rw");
            } catch (FileNotFoundException e) {
                throw new IllegalStateException(e);
            }
            channel = randomAccessFile.getChannel();
        }

        private void ensureFileExists(File file) {
            if (!file.exists()) {
                boolean wasDownloaded = metadataService.downloadIfPossible(file);
                if (!wasDownloaded) {
                    createEmptyFile(file);
                }
            }
        }

        private void createEmptyFile(File file) {
            final boolean created;
            try {
                created = file.createNewFile();
            } catch (IOException e1) {
                throw new IllegalStateException("unable to create file");
            }
            if (!created) {
                throw new IllegalStateException("file already existed");
            }
        }

        public void close() throws IOException {
            if (channel.isOpen()) {
                channel.force(true);
                channel.close();
            } else {
                logger.warn("closing bucket {} without an open channel.", bucketNumber);
            }
            randomAccessFile.close();
        }

        public long getBytes(ByteBuffer readInto, long offset, int length) throws IOException {
            final long offsetInThisBucket = offsetInThisBucket(offset);
            final long lengthInThisBucket = calcLengthInThisBucket(offsetInThisBucket, length);
            final int read;
            synchronized (this) {
                channel.position(offsetInThisBucket);
                read = channel.read(readInto);
            }
            if (read != lengthInThisBucket) {
                final byte[] zeroes;
                if (read == -1) {
                    zeroes = new byte[(int) lengthInThisBucket];
                } else {
                    zeroes = new byte[(int) (lengthInThisBucket - read)];
                }
                logger.debug("tried to read more bytes from this file than ever were written, replacing with {} zeroes", zeroes.length);
                readInto.put(zeroes);

            }
            return lengthInThisBucket;
        }

        @VisibleForTesting
        long calcLengthInThisBucket(long offsetInThisBucket, long length) {
            if (length < 1) {
                throw new UnsupportedOperationException("she said it's too small: " + length);
            } else if (offsetInThisBucket < 0) {
                throw new UnsupportedOperationException("unable to get offset " + offsetInThisBucket + " smaller than my base " + baseOffset);
            } else {
                final long consumableBytes = size - offsetInThisBucket;
                final long lenghtThisBucket = Math.min(consumableBytes, length);
                if (lenghtThisBucket < 0) {
                    throw new UnsupportedOperationException("unable to get offset " + offsetInThisBucket + " length is negative: " + lenghtThisBucket);
                }
                return lenghtThisBucket;
            }
        }

        public void flush() {
            try {
                //todo make sure this triggers after potentially different pending writes have their lock
                synchronized (this) {
                    if (channel.isOpen()) {
                        channel.force(true);
                    }
                }
            } catch (IOException e) {
                logger.warn("unable to flush file {}", filename);
            }
        }

        @Override
        public long putBytes(long offset, ByteBuffer message) throws IOException {
            synchronized (this) {
                final long offsetInThisBucket = offsetInThisBucket(offset);
                channel.position(offsetInThisBucket);
                return channel.write(message);
            }
        }

        private long offsetInThisBucket(long offset) {
            return offset - baseOffset;
        }

        @Override
        public void trim(long offset, long length) throws IOException {
            final long offsetInThisBucket = offsetInThisBucket(offset);
            final long lengthInThisBucket = calcLengthInThisBucket(offsetInThisBucket, length);
            if (lengthInThisBucket == size) {
                synchronized (this) {
                    channel.truncate(0);
                    channel.force(true);
                }
            } else {
                final ByteBuffer bb = ByteBuffer.allocate((int) length);
                bb.put(new byte[(int) length]);
                bb.flip();
                putBytes(offset, bb);
            }
        }

        @Override
        public long getBaseOffset() {
            return baseOffset;
        }

        @Override
        public long getUpperBound() {
            return upperBound;
        }

    }
}